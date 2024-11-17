from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import httpx
import asyncio
from datetime import datetime
import logging
import os
from dotenv import load_dotenv
from processor.ocr import process_tiff
from processor.classifier import classify_text
from processor.email_router import O365EmailRouter

# Load environment variables
load_dotenv()

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Initialize FastAPI
app = FastAPI(
    title="Fax Categorization",
    description="Service for categorizing faxes from HumbleFax"
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

email_router = O365EmailRouter()

class FaxPoller:
    def __init__(self):
        self.access_key = os.getenv("HUMBLE_FAX_ACCESS_KEY")
        self.secret_key = os.getenv("HUMBLE_FAX_SECRET_KEY")
        self.to_number = int(os.getenv("FAX_TO_NUMBER"))

        if not self.access_key or not self.secret_key or not self.to_number:
            raise ValueError("HUMBLE_FAX_ACCESS_KEY, HUMBLE_FAX_SECRET_KEY and FAX_TO_NUMBER must be set in environment variables")

        self.base_url = "https://api.humblefax.com"
        self.last_poll_time = int(datetime.now().timestamp())

    async def poll_for_faxes(self):
        """Poll HumbleFax API for new faxes"""
        try:
            now = int(datetime.now().timestamp())

            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.base_url}/incomingFaxes",
                    params={
                        "timeFrom": self.last_poll_time,
                        "timeTo": now,
                        "toNumber": self.to_number
                    },
                    auth=(self.access_key, self.secret_key)
                )
                response.raise_for_status()

                response_data = response.json()
                self.last_poll_time = now
                return response_data.get('data', {}).get('incomingFaxes', [])

        except Exception as e:
            logger.error(f"Error polling HumbleFax API: {str(e)}")
            return []

    async def download_fax(self, fax_id: str, file_format: str = "tiff"):
        """Download a specific fax as TIFF or PDF"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.base_url}/incomingFax/{fax_id}/download",
                    params={"fileFormat": file_format},
                    auth=(self.access_key, self.secret_key)
                )
                response.raise_for_status()
                logger.info(f"Fax {fax_id} Downloaded as {file_format}")
                return response.content
        except Exception as e:
            logger.error(f"Error downloading fax {fax_id}: {str(e)}")
            raise


async def process_new_faxes(poller):
    """Check for new faxes, download them, and process them"""
    try:
        # Initialize email router
        email_router = O365EmailRouter()

        # Load sender mappings from environment variables
        sender_mappings = {}
        mappings_str = os.getenv("SENDER_MAPPINGS", "")
        if mappings_str:
            for mapping in mappings_str.split(","):
                sender, doc_type = mapping.split(":")
                sender_mappings[sender.strip()] = doc_type.strip()

        now = int(datetime.now().timestamp())
        logger.info(f"Polling for new faxes at {now}")

        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{poller.base_url}/incomingFaxes",
                params={
                    "timeFrom": now - int(os.getenv("POLLING_RATE", "60")), # default to 60 if env field isn't set
                    "timeTo": now,
                    "toNumber": poller.to_number
                },
                auth=(poller.access_key, poller.secret_key)
            )
            response.raise_for_status()

            response_data = response.json()
            faxes = response_data.get('data', {}).get('incomingFaxes', [])

            if faxes:
                logger.info(f"Found {len(faxes)} new faxes")
                for fax in faxes:
                    fax_id = fax['id']
                    from_name = fax.get('fromNameAddressBook', '')
                    timestamp = int(fax['time']) if isinstance(fax['time'], str) else fax['time']
                    formatted_time = datetime.fromtimestamp(timestamp).strftime('%Y%m%d_%H%M%S')

                    pdf_filename = f"tmp/fax_{formatted_time}_{fax_id}.pdf"

                    try:
                        # Check if we have a known sender mapping
                        if from_name in sender_mappings:
                            # For known senders, just get PDF
                            content = await poller.download_fax(fax_id, "pdf")

                            with open(pdf_filename, 'wb') as f:
                                f.write(content)
                            logger.info(f"Successfully saved {pdf_filename}")

                            doc_type = sender_mappings[from_name]
                            logger.info(f"Direct classification from sender mapping: {doc_type}")
                            result = {
                                'classification': {
                                    'document_type': doc_type
                                }
                            }
                        else:
                            # Download both formats
                            try:
                                tiff_content = await poller.download_fax(fax_id, "tiff")
                                pdf_content = await poller.download_fax(fax_id, "pdf")

                                # Save PDF
                                with open(pdf_filename, 'wb') as f:
                                    f.write(pdf_content)
                                logger.info(f"Successfully saved {pdf_filename}")

                                try:
                                    # Process TIFF directly with OCR
                                    ocr_text = await process_tiff(tiff_content)

                                    try:
                                        # Get classification
                                        classification_result = await classify_text(ocr_text)
                                        result = {
                                            'classification': classification_result
                                        }
                                        logger.info(f"Successfully processed fax {fax_id}")
                                    except Exception as e:
                                        # return as Unknown in the event of failure
                                        logger.error(f"Classification failed for fax {fax_id}: {str(e)}")
                                        result = {
                                            'classification': {
                                                'document_type': 'Unknown'
                                            }
                                        }
                                except Exception as e:
                                    # return as Unknown in the event of failure
                                    logger.error(f"OCR failed for fax {fax_id}: {str(e)}")
                                    result = {
                                        'classification': {
                                            'document_type': 'Unknown'
                                        }
                                    }
                            except Exception as e:
                                # return as Unknown in the event of failure
                                logger.error(f"Failed to download fax {fax_id}: {str(e)}")
                                result = {
                                    'classification': {
                                        'document_type': 'Unknown'
                                    }
                                }

                        logger.info(
                            f"Classification result: {result.get('classification', {}).get('document_type', 'Unknown')}")

                        # Send email based on classification
                        if result.get('classification'):
                            doc_type = result['classification'].get('document_type')
                            email_sent = await email_router.send_fax_email(
                                document_type=doc_type,
                                pdf_path=pdf_filename,
                                fax_metadata=fax
                            )
                            if email_sent:
                                logger.info(f"Email sent successfully for fax {fax_id} ({doc_type})")
                            else:
                                logger.error(f"Failed to send email for fax {fax_id} ({doc_type})")

                    except Exception as e:
                        logger.error(f"Error processing fax {fax_id}: {str(e)}")
                        # Try to send as Unknown even if processing failed
                        try:
                            email_sent = await email_router.send_fax_email(
                                document_type="Unknown",
                                pdf_path=pdf_filename,
                                fax_metadata=fax
                            )
                            if email_sent:
                                logger.info(f"Email sent successfully for fax {fax_id} (Unknown - after error)")
                            else:
                                logger.error(f"Failed to send email for fax {fax_id} (Unknown - after error)")
                        except Exception as email_error:
                            logger.error(
                                f"Failed to send Unknown classification email for fax {fax_id}: {str(email_error)}")
                        continue

            else:
                logger.info("No new faxes found")

    except Exception as e:
        logger.error(f"Error checking for new faxes: {str(e)}")

async def polling_task():
    """Background task that polls for new faxes"""
    poller = FaxPoller()

    # Do initial poll immediately
    logger.info("Performing initial poll for faxes...")
    await process_new_faxes(poller)

    # Now start the regular polling
    while True:
        await asyncio.sleep(int(os.getenv("POLLING_RATE", "60")))  # Wait for POLLING_RATE, or 60 if not defined
        await process_new_faxes(poller)


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat()
    }


@app.on_event("startup")
async def startup_event():
    """Start the polling task when the app starts"""
    logger.info("Starting fax polling service")
    # Create tmp directory if it doesn't exist
    os.makedirs("tmp", exist_ok=True)
    asyncio.create_task(polling_task())


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True, log_level="info")