# processor/phi_redactor.py
from presidio_analyzer import AnalyzerEngine
from presidio_anonymizer import AnonymizerEngine
from presidio_analyzer.nlp_engine import SpacyNlpEngine
from typing import Dict, List
import logging

logger = logging.getLogger(__name__)


class PHIRedactor:
    def __init__(self):
        try:
            # Create configuration for medium model
            configuration = {
                "models": [{"lang_code": "en", "model_name": "en_core_web_md"}]
            }

            # Initialize SpaCy NLP Engine explicitly with medium model
            nlp_engine = SpacyNlpEngine(models=configuration["models"])

            # Initialize Presidio analyzer with specific NLP engine
            self.analyzer = AnalyzerEngine(nlp_engine=nlp_engine)
            self.anonymizer = AnonymizerEngine()

            # PHI entities to look for
            self.phi_entities = [
                "PERSON", "PHONE_NUMBER", "EMAIL_ADDRESS", "DATETIME", "ADDRESS",
                "MEDICAL_LICENSE", "LOCATION", "US_SSN", "IP_ADDRESS", "CREDIT_CARD",
                "US_DRIVER_LICENSE", "US_BANK_NUMBER", "US_ITIN", "US_PASSPORT",
                "MEDICAL_LICENSE", "ORGANIZATION", "NRP", "MRN"
            ]

            logger.info("PHI Redactor initialized successfully")
        except Exception as e:
            logger.error(f"Error initializing PHI Redactor: {str(e)}")
            raise

    async def redact_phi(self, text: str) -> Dict[str, str]:
        """
        Redact PHI from text while preserving the type of information redacted
        Returns both redacted text and a list of what was redacted
        """
        try:
            # Analyze text for PHI
            analyzer_results = self.analyzer.analyze(
                text=text,
                entities=self.phi_entities,
                language='en'
            )

            # Anonymize/redact the identified entities
            anonymized_text = self.anonymizer.anonymize(
                text=text,
                analyzer_results=analyzer_results
            )

            # Get summary of what was redacted
            redacted_elements = self._summarize_redactions(analyzer_results)

            redacted_info = {
                'redacted_text': anonymized_text.text,
                'redacted_elements': redacted_elements,
                'redaction_count': len(analyzer_results)
            }

            logger.info(f"Successfully redacted {len(analyzer_results)} PHI elements")
            return redacted_info

        except Exception as e:
            logger.error(f"Error redacting PHI: {str(e)}")
            # Return original text if redaction fails
            return {
                'redacted_text': text,
                'redacted_elements': [],
                'redaction_count': 0,
                'error': str(e)
            }

    def _summarize_redactions(self, analyzer_results) -> List[Dict]:
        """Create a summary of what types of information were redacted"""
        redacted_elements = []
        for result in analyzer_results:
            redacted_elements.append({
                'type': result.entity_type,
                'start': result.start,
                'end': result.end,
                'score': result.score
            })
        return redacted_elements

    async def is_phi_present(self, text: str) -> bool:
        """
        Quick check if PHI is present in text
        Useful for validation before sending to external APIs
        """
        try:
            results = self.analyzer.analyze(
                text=text,
                entities=self.phi_entities,
                language='en'
            )
            return len(results) > 0
        except Exception as e:
            logger.error(f"Error checking for PHI: {str(e)}")
            # Assume PHI might be present if check fails
            return True