import json
import os
import re

import anthropic

SYSTEM_PROMPT = (
    "You are an expert instructional designer for a financial services micro-learning platform. "
    "Your output MUST be valid JSON only. "
    "Do not include any explanation, markdown, or prose outside the JSON object."
)


def _build_user_prompt(raw_text: str, domain_names: list) -> str:
    domains_str = ' and '.join(f'[{d}]' for d in domain_names)
    return f"""You are creating training content for the domains of {domains_str}.

Chunk the following source text into 2-minute learning sprints. Each sprint should:
- Cover one coherent concept or skill
- Take approximately 2 minutes to read (roughly 250-300 words)
- Use clear, professional language appropriate for financial services professionals
- Emphasize practical application relating to {domains_str}

Return a JSON object with this EXACT structure (no extra keys):
{{
  "document_summary": "One sentence summary of the source document",
  "domains": {json.dumps(domain_names)},
  "total_modules": <integer>,
  "modules": [
    {{
      "sequence_order": 1,
      "title": "Short descriptive title (max 10 words)",
      "content": "Full sprint content (250-300 words)",
      "key_takeaway": "Single sentence the learner should remember",
      "reading_time_minutes": 2
    }}
  ]
}}

Source text:
---
{raw_text}
---"""


def _strip_code_fences(text: str) -> str:
    """Remove markdown code fences that some models add around JSON."""
    text = text.strip()
    text = re.sub(r'^```[a-z]*\n?', '', text)
    text = re.sub(r'\n?```$', '', text)
    return text.strip()


def generate_micro_modules(raw_text: str, domain_names: list) -> dict:
    api_key = os.environ.get('ANTHROPIC_API_KEY')
    if not api_key:
        raise RuntimeError('ANTHROPIC_API_KEY environment variable is not set.')

    client = anthropic.Anthropic(api_key=api_key)

    # Truncate to ~20K tokens of raw text to stay comfortably within limits
    truncated_text = raw_text[:80_000]

    message = client.messages.create(
        model='claude-haiku-4-5-20251001',
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        messages=[
            {'role': 'user', 'content': _build_user_prompt(truncated_text, domain_names)}
        ],
    )

    response_text = message.content[0].text
    response_text = _strip_code_fences(response_text)
    return json.loads(response_text)
