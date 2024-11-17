from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import httpx
from dotenv import load_dotenv
from processor.fax_processor import FaxProcessor
import asyncio
import os
import logging
from datetime import datetime

# Load environment variables
load_dotenv()

# init fax processor
fax_processor = None

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# Set specific logger levels
logging.getLogger('presidio-analyzer').setLevel(logging.ERROR)  # Only show errors from Presidio
logging.getLogger('watchfiles.main').setLevel(logging.WARNING)  # Reduce file watcher noise
logging.getLogger('uvicorn.access').setLevel(logging.WARNING)  # Reduce API access noise

# Keep our application logging at INFO level
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Keep phi_redactor logging at INFO level
logging.getLogger('processor.phi_redactor').setLevel(logging.INFO)
logging.getLogger('processor.fax_processor').setLevel(logging.INFO)
logging.getLogger('processor.email_router').setLevel(logging.INFO)

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


class FaxPoller:
    def __init__(self):
        # Validate required environment variables
        required_vars = ["HUMBLE_FAX_ACCESS_KEY", "HUMBLE_FAX_SECRET_KEY", "FAX_TO_NUMBER"]
        missing_vars = [var for var in required_vars if not os.getenv(var)]
        if missing_vars:
            raise ValueError(f"Missing required environment variables: {', '.join(missing_vars)}")

        self.access_key = os.getenv("HUMBLE_FAX_ACCESS_KEY")
        self.secret_key = os.getenv("HUMBLE_FAX_SECRET_KEY")
        self.to_number = int(os.getenv("FAX_TO_NUMBER"))
        self.base_url = "https://api.humblefax.com"

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
    """Check for new faxes and add them to processing queue"""
    try:
        now = int(datetime.now().timestamp())
        logger.info(f"Polling for new faxes at {now}")

        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{poller.base_url}/incomingFaxes",
                params={
                    "timeFrom": now - int(os.getenv("POLLING_RATE", "60")),
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
                    await fax_processor.add_fax_to_queue(fax)
            else:
                logger.info("No new faxes found")

    except Exception as e:
        logger.error(f"Error checking for new faxes: {str(e)}")


async def polling_task():
    """Background task that polls for new faxes"""
    try:
        poller = FaxPoller()

        # Initialize fax processor with poller
        global fax_processor
        fax_processor = FaxProcessor(poller)
        await fax_processor.start_processing()

        # Do initial poll immediately
        logger.info("Performing initial poll for faxes...")
        await process_new_faxes(poller)

        # Now start the regular polling
        while True:
            await asyncio.sleep(int(os.getenv("POLLING_RATE", "60")))
            await process_new_faxes(poller)
    except Exception as e:
        logger.error(f"Fatal error in polling task: {str(e)}")
        raise

async def cleanup_task():
    """Background task that periodically cleans up old temporary files"""
    while True:
        try:
            # Get current time
            now = datetime.now()

            # Check tmp directory for old files
            tmp_dir = "tmp"
            if os.path.exists(tmp_dir):
                for filename in os.listdir(tmp_dir):
                    if filename.endswith(".pdf"):
                        file_path = os.path.join(tmp_dir, filename)
                        # Get file modification time
                        mtime = datetime.fromtimestamp(os.path.getmtime(file_path))
                        # If file is older than 1 hour, delete it
                        if (now - mtime).total_seconds() > 3600:  # 1 hour in seconds
                            try:
                                os.remove(file_path)
                                logger.info(f"Cleaned up old file: {filename}")
                            except Exception as e:
                                logger.error(f"Error deleting old file {filename}: {str(e)}")

            # Sleep for 30 minutes before next cleanup
            await asyncio.sleep(1800)  # 30 minutes in seconds
        except Exception as e:
            logger.error(f"Error in cleanup task: {str(e)}")
            await asyncio.sleep(1800)  # Wait before retrying

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "processor_status": "running" if fax_processor and fax_processor.is_processing else "stopped"
    }




@app.on_event("startup")
async def startup_event():
    """Start the application and fax processor"""
    logger.info("Starting fax polling service")

    # Ensure tmp directory exists
    os.makedirs("tmp", exist_ok=True)

    # Validate critical environment variables at startup
    required_vars = [
        "HUMBLE_FAX_ACCESS_KEY",
        "HUMBLE_FAX_SECRET_KEY",
        "FAX_TO_NUMBER",
        "SMTP_USERNAME",
        "SMTP_PASSWORD",
        "DEFAULT_FROM_EMAIL",
        "EMAIL_MAPPINGS"
    ]

    missing_vars = [var for var in required_vars if not os.getenv(var)]
    if missing_vars:
        error_msg = f"Missing required environment variables: {', '.join(missing_vars)}"
        logger.error(error_msg)
        raise ValueError(error_msg)

    # Validate spaCy model is installed
    try:
        import spacy
        spacy.load("en_core_web_md")
    except Exception as e:
        logger.error(f"Required spaCy model not installed. Please run: python -m spacy download en_core_web_md")
        raise

    # Start polling task (which will initialize the processor)
    asyncio.create_task(polling_task())
    asyncio.create_task(cleanup_task())


@app.on_event("shutdown")
async def shutdown_event():
    """Shutdown the application gracefully"""
    global fax_processor
    if fax_processor:
        await fax_processor.stop_processing()


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True, log_level="info")