import anthropic
import logging
import os
from typing import Dict, Any

logger = logging.getLogger(__name__)


async def classify_text(text: str) -> Dict[str, Any]:
    """
    Classify the OCR text using Anthropic API

    Args:
        text: OCR text to classify

    Returns:
        Dictionary containing classification results
    """
    try:
        client = anthropic.Anthropic(
            api_key=os.getenv("ANTHROPIC_API_KEY")
        )

        # Get all configuration from environment variables
        categories = os.getenv("CLASSIFICATION_CATEGORIES", "").split(",")
        default_response = os.getenv("DEFAULT_RESPONSE", "Unknown")
        prompt_intro = os.getenv("PROMPT_INTRO",
            "Based on the provided text, classify the associated document by selecting only one of the following categories")
        prompt_instructions = os.getenv("PROMPT_INSTRUCTIONS",
            "Your response should be the exact name of the classification from the list above, and nothing more. " +
            "Do not include any explanations or additional text.")

        if not categories:
            raise ValueError("CLASSIFICATION_CATEGORIES must be set in environment variables")

        # Build category bullets
        category_bullets = "\n".join(f"•  {category}" for category in categories)

        # Prompt
        prompt = f"""
        {prompt_intro}

        {category_bullets}

        {prompt_instructions}
        If none of the above classifications match, simply return "{default_response}".
        Document text:
        {text[:2000]}  # Limiting text length for API
        """

        logger.info("Sending text to Anthropic API for classification")
        message = client.messages.create(
            model="claude-3-5-haiku-latest",
            max_tokens=1024,
            messages=[{
                "role": "user",
                "content": prompt
            }]
        )

        # Create a simple classification dictionary with the response
        classification = {
            "document_type": message.content[0].text.strip()
        }

        logger.info("Successfully classified document")
        return classification

    except Exception as e:
        logger.error(f"Error classifying text: {str(e)}")
        raise