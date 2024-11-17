import pytesseract
import logging
import asyncio
from PIL import Image
import io

logger = logging.getLogger(__name__)


async def process_tiff(tiff_data: bytes) -> str:
    """
    Process TIFF data directly using Tesseract

    Args:
        tiff_data: Raw TIFF file data

    Returns:
        Extracted text from all pages
    """
    try:
        # Open TIFF from bytes
        with io.BytesIO(tiff_data) as tiff_bytes:
            image = Image.open(tiff_bytes)

            # Get all pages from the TIFF
            pages = []
            try:
                for i in range(1000):  # Safety limit
                    if i > 0:
                        image.seek(i)
                    pages.append(image.copy())
            except EOFError:
                # We've reached the end of the frames
                pass

            # Process each page in parallel using existing process_page function
            texts = await asyncio.gather(*[
                process_page(page, i)
                for i, page in enumerate(pages)
            ])

            # Combine all text with page breaks
            full_text = "\n\n=== PAGE BREAK ===\n\n".join(texts)

            return full_text

    except Exception as e:
        logger.error(f"Error processing TIFF data: {str(e)}")
        raise


async def process_page(page, page_num: int) -> str:
    """Process a single page using Tesseract OCR"""
    try:
        # Run OCR in a thread pool to avoid blocking
        text = await asyncio.to_thread(
            pytesseract.image_to_string,
            page,
            lang='eng',
            config='--psm 6'  # Assume uniform block of text
        )

        logger.info(f"Successfully processed page {page_num + 1}")
        return text.strip()

    except Exception as e:
        logger.error(f"Error processing page {page_num + 1}: {str(e)}")
        raise