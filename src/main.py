from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import httpx
import asyncio
from datetime import datetime
import logging
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Initialize FastAPI app
app = FastAPI(
    title="Fax Processing Service",
    description="Service for processing faxes from HumbleFax"
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
        self.access_key = os.getenv("HUMBLE_FAX_ACCESS_KEY")
        self.secret_key = os.getenv("HUMBLE_FAX_SECRET_KEY")

        if not self.access_key or not self.secret_key:
            raise ValueError("HUMBLE_FAX_ACCESS_KEY and HUMBLE_FAX_SECRET_KEY must be set in environment variables")

        self.base_url = "https://api.humblefax.com"
        self.to_number = 17064804185 # UDSCC's fax number (maybe store in the .env file)
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

                # First convert response to JSON
                response_data = response.json()

                # Update last poll time only after successful request
                self.last_poll_time = now

                # Return the incomingFaxes array from the nested data structure
                return response_data.get('data', {}).get('incomingFaxes', [])

        except Exception as e:
            logger.error(f"Error polling HumbleFax API: {str(e)}")
            return []

    async def download_fax(self, fax_id: str):
        """Download a specific fax as PDF"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.base_url}/incomingFax/{fax_id}/download",
                    params={"fileFormat": "pdf"},
                    auth=(self.access_key, self.secret_key)
                )
                response.raise_for_status()
                logger.info(f"Fax {fax_id} Downloaded")
                return response.content
        except Exception as e:
            logger.error(f"Error downloading fax {fax_id}: {str(e)}")
            raise


async def check_for_new_faxes(poller):
    """Check for new faxes and download them"""
    try:
        now = int(datetime.now().timestamp())
        logger.info(f"Polling for new faxes at {now}")

        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{poller.base_url}/incomingFaxes",
                params={
                    "timeFrom": now - 60,  # Look back 1 minute
                    "timeTo": now,
                    "toNumber": poller.to_number
                },
                auth=(poller.access_key, poller.secret_key)
            )
            response.raise_for_status()

            response_data = response.json()
            # Access the nested incomingFaxes array
            faxes = response_data.get('data', {}).get('incomingFaxes', [])

            if faxes:
                logger.info(f"Found {len(faxes)} new faxes")
                for fax in faxes:
                    fax_id = fax['id']
                    # Convert timestamp from string to int if needed
                    timestamp = int(fax['time']) if isinstance(fax['time'], str) else fax['time']
                    formatted_time = datetime.fromtimestamp(timestamp).strftime('%Y%m%d_%H%M%S')
                    filename = f"downloads/fax_{formatted_time}_{fax_id}.pdf"

                    if not os.path.exists(filename):
                        logger.info(f"Downloading new fax {fax_id}")
                        content = await poller.download_fax(fax_id)
                        with open(filename, 'wb') as f:
                            f.write(content)
                        logger.info(f"Successfully saved {filename}")
            else:
                logger.info("No new faxes found")

    except Exception as e:
        logger.error(f"Error checking for new faxes: {str(e)}")


async def polling_task():
    """Background task that polls for new faxes"""
    poller = FaxPoller()

    # Do initial poll immediately
    logger.info("Performing initial poll for faxes...")
    await check_for_new_faxes(poller)

    # Now start the regular polling
    while True:
        await asyncio.sleep(60)  # Wait for 60 seconds between polls
        await check_for_new_faxes(poller)


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
    # Create downloads directory if it doesn't exist
    os.makedirs("downloads", exist_ok=True)
    asyncio.create_task(polling_task())


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", 8000))

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=port,
        reload=True,
        log_level="info"
    )