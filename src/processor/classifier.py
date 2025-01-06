# classifier.py
import anthropic
import logging
import os
from typing import Dict, Any

logger = logging.getLogger(__name__)


async def classify_text(text: str) -> Dict[str, Any]:
    """
    Classify the OCR text using Anthropic API with keyword awareness

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
        default_response = os.getenv("DEFAULT_RESPONSE", "Uncategorized")
        keyword_rules = os.getenv("KEYWORD_RULES", "").split(",")
        keyword_rules_additional = os.getenv("KEYWORD_RULES_ADDITIONAL", "").split(",")

        if not categories:
            raise ValueError("CLASSIFICATION_CATEGORIES must be set in environment variables")

        # Build category bullets
        category_bullets = "\n".join(f"•  {category}" for category in categories)

        # Build keyword rules
        keyword_instructions = "\n".join(f"•  {rule}" for rule in keyword_rules)

        # prompt with keyword rules
        prompt = f"""
        {os.getenv("PROMPT_INTRO", "Based on the provided text, classify the associated document by selecting only one of the following categories")}

        Categories:
        {category_bullets}
        
        {os.getenv("PROMPT_INSTRUCTIONS", "Your response should be the exact name of the classification from the list above, and nothing more. Do not include any explanations or additional text.")}

        Pay special attention to these keyword rules:
        {keyword_instructions}
        {keyword_rules_additional}

        If none of the above classifications match, return "{default_response}".

        Document text:
        {text[:4000]}
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

        classification = {
            "document_type": message.content[0].text.strip()
        }

        logger.info("Successfully classified document")
        return classification

    except Exception as e:
        logger.error(f"Error classifying text: {str(e)}")
        raise