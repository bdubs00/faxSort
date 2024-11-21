# processor/fax_processor.py
import os
import asyncio
import logging
from datetime import datetime
from typing import Dict, Optional
from .email_router import O365EmailRouter
from .phi_redactor import PHIRedactor
from .ocr import process_tiff
from .classifier import classify_text


logger = logging.getLogger(__name__)


class FaxProcessor:
    def __init__(self, poller):
        """
        Initialize FaxProcessor

        Args:
            poller: FaxPoller instance for making API calls
        """
        self.poller = poller  # Store poller instance
        self.email_router = O365EmailRouter()
        self.processing_queue = asyncio.Queue()
        self.is_processing = False
        self.phi_redactor = PHIRedactor() if os.getenv("HIPAA_MODE", "false").lower() == "true" else None

        # Load sender mappings
        self.sender_mappings = {}
        mappings_str = os.getenv("SENDER_MAPPINGS", "")
        if mappings_str:
            for mapping in mappings_str.split(","):
                sender, doc_type = mapping.split(":")
                self.sender_mappings[sender.strip()] = doc_type.strip()

    async def start_processing(self):
        """Start the background processing task"""
        self.is_processing = True
        asyncio.create_task(self._process_queue())

    async def stop_processing(self):
        """Stop the background processing task"""
        self.is_processing = False
        # Wait for queue to empty
        while not self.processing_queue.empty():
            await asyncio.sleep(1)

    async def add_fax_to_queue(self, fax: Dict):
        """Add a fax to the processing queue"""
        await self.processing_queue.put(fax)
        logger.info(f"Added fax {fax['id']} to processing queue")

    async def _process_queue(self):
        """Background task to process faxes from the queue"""
        while self.is_processing:
            try:
                if not self.processing_queue.empty():
                    fax = await self.processing_queue.get()
                    await self._process_single_fax(fax)
                    self.processing_queue.task_done()
                else:
                    await asyncio.sleep(1)
            except Exception as e:
                logger.error(f"Error in queue processing: {str(e)}")
                await asyncio.sleep(1)

    async def _process_single_fax(self, fax: Dict):
        """Process a single fax"""
        try:
            fax_id = fax['id']
            from_name = fax.get('fromNameAddressBook', '')
            timestamp = int(fax['time']) if isinstance(fax['time'], str) else fax['time']
            formatted_time = datetime.fromtimestamp(timestamp).strftime('%Y%m%d_%H%M%S')
            pdf_filename = f"tmp/fax_{formatted_time}_{fax_id}.pdf"

            # Process based on sender mapping or OCR
            if from_name in self.sender_mappings:
                result = await self._process_known_sender(fax_id, from_name, pdf_filename)
            else:
                result = await self._process_unknown_sender(fax_id, pdf_filename)

            # Send email
            if result and result.get('classification'):
                await self._send_email(fax_id, result, pdf_filename, fax)

        except Exception as e:
            logger.error(f"Error processing fax {fax_id}: {str(e)}")
            await self._handle_processing_failure(fax_id, pdf_filename, fax)

    async def _process_known_sender(self, fax_id: str, from_name: str, pdf_filename: str) -> Dict:
        """Process fax from known sender"""
        try:
            content = await self.poller.download_fax(fax_id, "pdf")
            with open(pdf_filename, 'wb') as f:
                f.write(content)

            doc_type = self.sender_mappings[from_name]
            logger.info(f"Direct classification from sender mapping: {doc_type}")

            return {
                'classification': {
                    'document_type': doc_type
                }
            }
        except Exception as e:
            logger.error(f"Error processing known sender fax {fax_id}: {str(e)}")
            return None

    async def _process_unknown_sender(self, fax_id: str, pdf_filename: str) -> Dict:
        """Process fax from unknown sender"""
        try:
            # Download both formats
            tiff_content = await self.poller.download_fax(fax_id, "tiff")
            pdf_content = await self.poller.download_fax(fax_id, "pdf")

            # Save PDF
            with open(pdf_filename, 'wb') as f:
                f.write(pdf_content)

            # Process with OCR
            ocr_text = await process_tiff(tiff_content)

            # Redact PHI if enabled
            if self.phi_redactor:
                redaction_result = await self.phi_redactor.redact_phi(ocr_text)
                ocr_text = redaction_result['redacted_text']

            # Classify
            classification_result = await classify_text(ocr_text)
            return {'classification': classification_result}

        except Exception as e:
            logger.error(f"Error processing unknown sender fax {fax_id}: {str(e)}")
            return None

    async def _cleanup_pdf(self, pdf_path: str):
        """Delete PDF file after successful processing"""
        try:
            if os.path.exists(pdf_path):
                os.remove(pdf_path)
                logger.info(f"Successfully deleted temporary file: {pdf_path}")
        except Exception as e:
            logger.error(f"Error deleting PDF file {pdf_path}: {str(e)}")

    async def _send_email(self, fax_id: str, result: Dict, pdf_filename: str, fax_metadata: Dict):
        """Send email with classification result and cleanup PDF after success"""
        try:
            doc_type = result['classification'].get('document_type', 'Uncategorized')
            email_sent = await self.email_router.send_fax_email(
                document_type=doc_type,
                pdf_path=pdf_filename,
                fax_metadata=fax_metadata
            )
            if email_sent:
                logger.info(f"Email sent successfully for fax {fax_id} ({doc_type})")
                # Clean up PDF file after successful email
                await self._cleanup_pdf(pdf_filename)
            else:
                logger.error(f"Failed to send email for fax {fax_id} ({doc_type})")
        except Exception as e:
            logger.error(f"Error sending email for fax {fax_id}: {str(e)}")

    async def _handle_processing_failure(self, fax_id: str, pdf_filename: str, fax_metadata: Dict):
        """Handle any processing failures by sending as Uncategorized and cleaning up"""
        try:
            email_sent = await self.email_router.send_fax_email(
                document_type="Uncategorized",
                pdf_path=pdf_filename,
                fax_metadata=fax_metadata
            )
            if email_sent:
                logger.info(f"Sent failure notification email for fax {fax_id}")
                # Clean up PDF file after successful email
                await self._cleanup_pdf(pdf_filename)
            else:
                logger.error(f"Failed to send failure notification for fax {fax_id}")
        except Exception as e:
            logger.error(f"Failed to send failure notification for fax {fax_id}: {str(e)}")