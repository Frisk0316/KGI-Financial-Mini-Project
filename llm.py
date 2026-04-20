import json
import os
import re


DEFAULT_MODEL = 'gpt-5.4-mini'

SYSTEM_PROMPT = (
    'You are an expert instructional designer for a financial services micro-learning platform. '
    'Transform source material into concise, accurate, job-relevant learning sprints. '
    'Return valid JSON only and strictly follow the requested schema.'
)

RESPONSE_SCHEMA = {
    'type': 'object',
    'additionalProperties': False,
    'properties': {
        'document_summary': {'type': 'string'},
        'domains': {
            'type': 'array',
            'items': {'type': 'string'},
        },
        'total_modules': {'type': 'integer'},
        'modules': {
            'type': 'array',
            'minItems': 1,
            'items': {
                'type': 'object',
                'additionalProperties': False,
                'properties': {
                    'sequence_order': {'type': 'integer'},
                    'title': {'type': 'string'},
                    'content': {'type': 'string'},
                    'key_takeaway': {'type': 'string'},
                    'reading_time_minutes': {'type': 'number'},
                },
                'required': [
                    'sequence_order',
                    'title',
                    'content',
                    'key_takeaway',
                    'reading_time_minutes',
                ],
            },
        },
    },
    'required': ['document_summary', 'domains', 'total_modules', 'modules'],
}


class LLMConfigurationError(RuntimeError):
    pass


class LLMServiceError(RuntimeError):
    pass


def validate_micro_modules_payload(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise ValueError('The AI response must be a JSON object.')

    modules = payload.get('modules')
    if not isinstance(modules, list) or not modules:
        raise ValueError('The AI response must include at least one module.')

    validated_modules = []
    for index, module in enumerate(modules, start=1):
        if not isinstance(module, dict):
            raise ValueError(f'Module #{index} must be an object.')

        title = str(module.get('title', '')).strip()
        content = str(module.get('content', '')).strip()
        key_takeaway = str(module.get('key_takeaway', '')).strip()
        sequence_order = module.get('sequence_order', index)
        reading_time = module.get('reading_time_minutes', 2)

        if not title:
            raise ValueError(f'Module #{index} is missing a title.')
        if not content:
            raise ValueError(f'Module #{index} is missing content.')

        try:
            sequence_order = int(sequence_order)
        except (TypeError, ValueError) as exc:
            raise ValueError(f'Module #{index} has an invalid sequence_order.') from exc

        try:
            reading_time = float(reading_time)
        except (TypeError, ValueError) as exc:
            raise ValueError(f'Module #{index} has an invalid reading_time_minutes.') from exc

        if reading_time <= 0:
            raise ValueError(f'Module #{index} must have a positive reading_time_minutes value.')

        validated_modules.append({
            'sequence_order': sequence_order,
            'title': title,
            'content': content,
            'key_takeaway': key_takeaway,
            'reading_time_minutes': reading_time,
        })

    summary = str(payload.get('document_summary', '')).strip()
    total_modules = payload.get('total_modules', len(validated_modules))
    try:
        total_modules = int(total_modules)
    except (TypeError, ValueError) as exc:
        raise ValueError('The AI response has an invalid total_modules value.') from exc

    if total_modules != len(validated_modules):
        raise ValueError('The AI response total_modules does not match the module list.')

    returned_domains = payload.get('domains', [])
    if not isinstance(returned_domains, list):
        raise ValueError('The AI response domains field must be an array.')

    return {
        'document_summary': summary,
        'domains': [str(domain).strip() for domain in returned_domains],
        'total_modules': total_modules,
        'modules': validated_modules,
    }


def _build_user_prompt(raw_text: str, domain_names: list[str]) -> str:
    domains_str = ', '.join(domain_names)
    return f"""You are creating training content for the domains: {domains_str}.

Create 2-minute learning sprints from the source text below.

Requirements:
- Keep the content faithful to the source text.
- Use clear, professional language for financial services staff.
- Emphasize practical application for the selected domains: {domains_str}.
- Split the material into coherent modules.
- Each module should target about 2 minutes of reading time.
- Produce concise titles and one key takeaway per module.
- Return the selected domains exactly as provided.

Source text:
---
{raw_text}
---"""


def _request_structured_output(api_key: str, model: str, prompt: str) -> dict:
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise LLMConfigurationError(
            'The openai package is not installed. Run "pip install -r requirements.txt".'
        ) from exc

    try:
        client = OpenAI(api_key=api_key)
        response = client.responses.create(
            model=model,
            instructions=SYSTEM_PROMPT,
            input=prompt,
            text={
                'format': {
                    'type': 'json_schema',
                    'name': 'micro_modules',
                    'strict': True,
                    'schema': RESPONSE_SCHEMA,
                }
            },
        )
    except Exception as exc:
        raise LLMServiceError(f'LLM request failed: {exc}') from exc

    response_text = getattr(response, 'output_text', '').strip()
    if not response_text:
        raise LLMServiceError('LLM returned an empty response.')

    try:
        return json.loads(response_text)
    except json.JSONDecodeError as exc:
        raise LLMServiceError('LLM returned invalid JSON.') from exc


def generate_micro_modules(raw_text: str, domain_names: list[str]) -> dict:
    api_key = os.environ.get('OPENAI_API_KEY')
    if not api_key:
        raise LLMConfigurationError('OPENAI_API_KEY environment variable is not set.')

    model = os.environ.get('OPENAI_MODEL', DEFAULT_MODEL).strip() or DEFAULT_MODEL
    truncated_text = raw_text[:80_000]
    prompt = _build_user_prompt(truncated_text, domain_names)
    payload = _request_structured_output(api_key, model, prompt)
    validated = validate_micro_modules_payload(payload)

    if validated['domains'] != domain_names:
        raise ValueError('The AI response domains do not match the selected domains.')

    return validated
