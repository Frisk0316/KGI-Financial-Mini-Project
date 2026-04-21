import json
import os
import re
import time


DEFAULT_MODEL = 'gpt-5.4-mini'
TARGET_READING_TIME_MINUTES = 2.0
MOCK_LLM_ENABLED_VALUES = {'1', 'true', 'yes', 'on'}
DEFAULT_OPENAI_MAX_RETRIES = 4
RETRYABLE_STATUS_CODES = {408, 409, 429, 500, 502, 503, 504}
DEFAULT_RATE_LIMIT_DELAY_SECONDS = 20.0
DEFAULT_TRANSIENT_DELAY_SECONDS = 2.0
MAX_DOC_PROMPT_CHARS = 18_000
MAX_TOTAL_PROMPT_CHARS = 90_000

SYSTEM_PROMPT = (
    'You are an expert instructional designer for a financial services micro-learning platform. '
    'Read the provided materials carefully, synthesize across related documents when useful, and '
    'return valid JSON only.'
)

DOMAIN_PROFILES = {
    'Compliance': {
        'title_en': 'Compliance Controls and Operating Discipline',
        'title_zh': '合規要求與作業控管',
        'takeaway_en': 'Clarify the regulatory duty, control checkpoints, and disclosure expectations before acting.',
        'takeaway_zh': '先釐清法遵義務、控制節點與揭露要求，再安排後續行動。',
    },
    'CRM': {
        'title_en': 'Client Communication and Follow-Up Priorities',
        'title_zh': '客戶溝通與追蹤重點',
        'takeaway_en': 'Translate the material into clear client communication steps and service follow-up actions.',
        'takeaway_zh': '把文件內容轉成清楚的客戶溝通步驟與服務追蹤動作。',
    },
    'TaxRegulations': {
        'title_en': 'Tax Rules and Filing Reminders',
        'title_zh': '稅務規則與申報提醒',
        'takeaway_en': 'Confirm the tax basis, filing timing, and reporting implications before making recommendations.',
        'takeaway_zh': '先確認課稅基礎、申報時點與揭露影響，再提出建議。',
    },
    'LifeInsurance': {
        'title_en': 'Life Insurance Coverage and Policy Essentials',
        'title_zh': '壽險保障與保單重點',
        'takeaway_en': 'Frame the module around protection needs, policy structure, and beneficiary implications.',
        'takeaway_zh': '以保障需求、保單架構與受益人影響作為說明主軸。',
    },
    'InvestmentLinked': {
        'title_en': 'Investment-Linked Product and Allocation Essentials',
        'title_zh': '投資型商品與配置重點',
        'takeaway_en': 'Explain the product structure, underlying assets, and risk-return tradeoffs before discussing allocation.',
        'takeaway_zh': '先說明商品結構、底層資產與風險報酬，再談配置方式。',
    },
    'WealthManagement': {
        'title_en': 'Wealth Planning and Asset Strategy',
        'title_zh': '財富管理與資產策略',
        'takeaway_en': 'Connect the module to broader asset goals, succession planning, and long-term client decisions.',
        'takeaway_zh': '把內容連回整體資產目標、傳承規劃與長期決策。',
    },
    'Other': {
        'title_en': 'General Operational Knowledge',
        'title_zh': '其他通用知識',
        'takeaway_en': 'When the content does not fit a named domain, distill it into practical operating guidance.',
        'takeaway_zh': '當內容不完全落在既有標籤時，整理成可執行的通用作業知識。',
    },
}

SUMMARY_SCHEMA = {
    'type': 'object',
    'additionalProperties': False,
    'properties': {
        'batch_summary': {'type': 'string'},
        'documents': {
            'type': 'array',
            'minItems': 1,
            'items': {
                'type': 'object',
                'additionalProperties': False,
                'properties': {
                    'doc_id': {'type': 'integer'},
                    'file_name': {'type': 'string'},
                    'summary': {'type': 'string'},
                    'key_points': {
                        'type': 'array',
                        'minItems': 1,
                        'items': {'type': 'string'},
                    },
                },
                'required': ['doc_id', 'file_name', 'summary', 'key_points'],
            },
        },
    },
    'required': ['batch_summary', 'documents'],
}

MODULES_SCHEMA = {
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
                    'source_doc_ids': {
                        'type': 'array',
                        'minItems': 1,
                        'items': {'type': 'integer'},
                    },
                },
                'required': [
                    'sequence_order',
                    'title',
                    'content',
                    'key_takeaway',
                    'reading_time_minutes',
                    'source_doc_ids',
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


def _read_positive_int_env(name, default):
    raw_value = os.environ.get(name, '').strip()
    if not raw_value:
        return default

    try:
        parsed = int(raw_value)
    except ValueError:
        return default

    return parsed if parsed > 0 else default


def _is_mock_llm_enabled():
    return os.environ.get('MOCK_LLM', '').strip().lower() in MOCK_LLM_ENABLED_VALUES


def _normalize_whitespace(text):
    return re.sub(r'\s+', ' ', str(text or '')).strip()


def _truncate_text(text, limit):
    normalized = _normalize_whitespace(text)
    if len(normalized) <= limit:
        return normalized
    return f'{normalized[:limit - 3].rstrip()}...'


def _contains_cjk(text):
    return bool(re.search(r'[\u3400-\u9fff]', str(text or '')))


def _split_sentences(text):
    return [
        part.strip()
        for part in re.split(r'(?<=[.!?。！？])\s+|\n+', str(text or ''))
        if part.strip()
    ]


def _select_key_sentences(text, max_items=3):
    selected = []
    seen = set()

    for sentence in _split_sentences(text):
        cleaned = _truncate_text(sentence.strip(' -'), 140)
        if len(cleaned) < 12:
            continue
        signature = cleaned.lower()
        if signature in seen:
            continue
        seen.add(signature)
        selected.append(cleaned)
        if len(selected) >= max_items:
            return selected

    fragments = re.split(r'[;；•]', str(text or ''))
    for fragment in fragments:
        cleaned = _truncate_text(fragment.strip(' -'), 120)
        if len(cleaned) < 12:
            continue
        signature = cleaned.lower()
        if signature in seen:
            continue
        seen.add(signature)
        selected.append(cleaned)
        if len(selected) >= max_items:
            break

    return selected


def _build_focus_phrase(custom_prompt, prefer_cjk):
    cleaned = _truncate_text(custom_prompt, 80).strip(' .;。；')
    if cleaned:
        return cleaned
    if prefer_cjk:
        return '實務應用與客戶溝通'
    return 'practical application and client communication'


def _primary_domain(domain_names):
    for name in domain_names:
        if name in DOMAIN_PROFILES:
            return name
    return 'Other'


def _profile_for(domain_names):
    return DOMAIN_PROFILES.get(_primary_domain(domain_names), DOMAIN_PROFILES['Other'])


def _create_openai_client(api_key):
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise LLMConfigurationError(
            'The openai package is not installed. Run "pip install -r requirements.txt".'
        ) from exc
    return OpenAI(api_key=api_key)


def _is_retryable_llm_error(exc):
    status_code = getattr(exc, 'status_code', None)
    if status_code in RETRYABLE_STATUS_CODES:
        return True

    message = str(exc).lower()
    return any(
        marker in message
        for marker in (
            'connection error',
            'timed out',
            'timeout',
            'temporarily unavailable',
            'rate limit',
            'rate_limit_exceeded',
            'server error',
        )
    )


def _extract_retry_delay_seconds(exc):
    response = getattr(exc, 'response', None)
    headers = getattr(response, 'headers', None) or {}

    retry_after_ms = headers.get('retry-after-ms')
    if retry_after_ms:
        try:
            return max(float(retry_after_ms) / 1000, 0.5)
        except (TypeError, ValueError):
            pass

    retry_after = headers.get('retry-after')
    if retry_after:
        try:
            return max(float(retry_after), 0.5)
        except (TypeError, ValueError):
            pass

    message = str(exc)
    ms_match = re.search(r'try again in\s+(\d+(?:\.\d+)?)\s*ms', message, flags=re.IGNORECASE)
    if ms_match:
        return max(float(ms_match.group(1)) / 1000, 0.5)

    sec_match = re.search(r'try again in\s+(\d+(?:\.\d+)?)\s*s', message, flags=re.IGNORECASE)
    if sec_match:
        return max(float(sec_match.group(1)), 0.5)

    return None


def _calculate_retry_delay_seconds(exc, attempt):
    explicit_delay = _extract_retry_delay_seconds(exc)
    if explicit_delay is not None:
        return explicit_delay

    message = str(exc).lower()
    status_code = getattr(exc, 'status_code', None)
    if status_code == 429 or 'rate limit' in message or 'rate_limit_exceeded' in message:
        return DEFAULT_RATE_LIMIT_DELAY_SECONDS

    return min(DEFAULT_TRANSIENT_DELAY_SECONDS * attempt, 10.0)


def _request_structured_output(api_key, model, prompt, schema_name, schema):
    client = _create_openai_client(api_key)
    max_attempts = _read_positive_int_env('OPENAI_MAX_RETRIES', DEFAULT_OPENAI_MAX_RETRIES)

    response = None
    for attempt in range(1, max_attempts + 1):
        try:
            response = client.responses.create(
                model=model,
                instructions=SYSTEM_PROMPT,
                input=prompt,
                text={
                    'format': {
                        'type': 'json_schema',
                        'name': schema_name,
                        'strict': True,
                        'schema': schema,
                    }
                },
            )
            break
        except Exception as exc:
            if attempt < max_attempts and _is_retryable_llm_error(exc):
                time.sleep(_calculate_retry_delay_seconds(exc, attempt))
                continue

            attempt_label = 'attempt' if attempt == 1 else 'attempts'
            raise LLMServiceError(f'LLM request failed after {attempt} {attempt_label}: {exc}') from exc

    response_text = getattr(response, 'output_text', '').strip()
    if not response_text:
        raise LLMServiceError('LLM returned an empty response.')

    try:
        return json.loads(response_text)
    except json.JSONDecodeError as exc:
        raise LLMServiceError('LLM returned invalid JSON.') from exc


def _build_document_corpus(documents):
    pieces = []
    remaining = MAX_TOTAL_PROMPT_CHARS

    for document in documents:
        if remaining <= 0:
            break

        raw_text = document.get('raw_text', '')
        excerpt = _truncate_text(raw_text, min(MAX_DOC_PROMPT_CHARS, remaining))
        pieces.append(
            f"[Document {int(document['doc_id'])}] {document['file_name']}\n"
            f"{excerpt}"
        )
        remaining -= len(excerpt)

    return '\n\n'.join(pieces)


def _build_stage_one_prompt(documents, domain_names, custom_prompt=''):
    domains_str = ', '.join(domain_names)
    custom_prompt_block = (
        f'\nAdditional user instructions:\n{custom_prompt.strip()}\n'
        if custom_prompt.strip()
        else ''
    )
    return (
        f'You are analyzing a related document batch for the domains: {domains_str}.\n\n'
        'Read all documents first and then return JSON with:\n'
        '1. one combined batch summary\n'
        '2. one concise summary per document\n'
        '3. two to four key points per document\n'
        'Use the exact doc_id and file_name values found in the source blocks.\n'
        f'{custom_prompt_block}\n'
        'Source documents:\n'
        f'{_build_document_corpus(documents)}'
    )


def _validate_batch_summary_payload(payload, documents):
    if not isinstance(payload, dict):
        raise ValueError('The AI summary response must be a JSON object.')

    batch_summary = _normalize_whitespace(payload.get('batch_summary', ''))
    returned_documents = payload.get('documents')
    if not isinstance(returned_documents, list) or not returned_documents:
        raise ValueError('The AI summary response must include one summary per document.')

    expected_doc_ids = {int(document['doc_id']) for document in documents}
    expected_files = {int(document['doc_id']): document['file_name'] for document in documents}
    validated_documents = []
    seen_doc_ids = set()

    for item in returned_documents:
        if not isinstance(item, dict):
            raise ValueError('Each document summary must be an object.')

        try:
            doc_id = int(item.get('doc_id'))
        except (TypeError, ValueError) as exc:
            raise ValueError('Each document summary must include a valid doc_id.') from exc

        if doc_id not in expected_doc_ids:
            raise ValueError(f'The AI summary response referenced unexpected doc_id {doc_id}.')
        if doc_id in seen_doc_ids:
            raise ValueError(f'The AI summary response duplicated doc_id {doc_id}.')

        file_name = _normalize_whitespace(item.get('file_name', ''))
        summary = _normalize_whitespace(item.get('summary', ''))
        key_points = item.get('key_points')

        if file_name != expected_files[doc_id]:
            raise ValueError(f'The AI summary response returned the wrong file_name for doc_id {doc_id}.')
        if not summary:
            raise ValueError(f'The AI summary response is missing summary text for doc_id {doc_id}.')
        if not isinstance(key_points, list) or not key_points:
            raise ValueError(f'The AI summary response must include key_points for doc_id {doc_id}.')

        normalized_key_points = [
            _normalize_whitespace(point)
            for point in key_points
            if _normalize_whitespace(point)
        ]
        if not normalized_key_points:
            raise ValueError(f'The AI summary response returned empty key_points for doc_id {doc_id}.')

        validated_documents.append({
            'doc_id': doc_id,
            'file_name': file_name,
            'summary': summary,
            'key_points': normalized_key_points[:4],
        })
        seen_doc_ids.add(doc_id)

    if seen_doc_ids != expected_doc_ids:
        raise ValueError('The AI summary response did not return all requested documents.')

    return {
        'batch_summary': batch_summary,
        'documents': validated_documents,
    }


def _build_stage_two_prompt(summary_payload, domain_names, custom_prompt=''):
    domains_str = ', '.join(domain_names)
    custom_prompt_block = (
        f'\nAdditional user instructions:\n{custom_prompt.strip()}\n'
        if custom_prompt.strip()
        else ''
    )
    return (
        f'You are creating integrated micro-learning modules for the domains: {domains_str}.\n\n'
        'Use the multi-document summaries below and return JSON that:\n'
        '- synthesizes across related documents when useful\n'
        '- keeps the selected domains exactly as provided\n'
        '- returns source_doc_ids for every module\n'
        '- targets roughly 2 minutes of reading time per module\n'
        '- stays practical, concise, and job-relevant\n'
        f'{custom_prompt_block}\n'
        'Summary payload:\n'
        f'{json.dumps(summary_payload, ensure_ascii=False, indent=2)}'
    )


def validate_micro_modules_payload(payload, expected_domains, valid_doc_ids):
    if not isinstance(payload, dict):
        raise ValueError('The AI response must be a JSON object.')

    modules = payload.get('modules')
    if not isinstance(modules, list) or not modules:
        raise ValueError('The AI response must include at least one module.')

    returned_domains = payload.get('domains')
    if not isinstance(returned_domains, list):
        raise ValueError('The AI response domains field must be an array.')

    normalized_domains = [_normalize_whitespace(name) for name in returned_domains]
    if normalized_domains != expected_domains:
        raise ValueError('The AI response domains do not match the selected domains.')

    valid_doc_ids_set = {int(doc_id) for doc_id in valid_doc_ids}
    validated_modules = []
    seen_sequence_orders = set()

    for index, module in enumerate(modules, start=1):
        if not isinstance(module, dict):
            raise ValueError(f'Module #{index} must be an object.')

        title = _normalize_whitespace(module.get('title', ''))
        content = _normalize_whitespace(module.get('content', ''))
        key_takeaway = _normalize_whitespace(module.get('key_takeaway', ''))
        source_doc_ids = module.get('source_doc_ids')

        if not title:
            raise ValueError(f'Module #{index} is missing a title.')
        if not content:
            raise ValueError(f'Module #{index} is missing content.')

        try:
            sequence_order = int(module.get('sequence_order', index))
        except (TypeError, ValueError) as exc:
            raise ValueError(f'Module #{index} has an invalid sequence_order.') from exc

        if sequence_order <= 0:
            raise ValueError(f'Module #{index} must use a positive sequence_order.')
        if sequence_order in seen_sequence_orders:
            raise ValueError(f'Module #{index} reuses sequence_order {sequence_order}.')
        seen_sequence_orders.add(sequence_order)

        try:
            reading_time = float(module.get('reading_time_minutes', TARGET_READING_TIME_MINUTES))
        except (TypeError, ValueError) as exc:
            raise ValueError(f'Module #{index} has an invalid reading_time_minutes.') from exc

        if reading_time < 1 or reading_time > 3:
            raise ValueError(
                f'Module #{index} must keep reading_time_minutes within the 2-minute sprint range (1-3 minutes).'
            )

        if not isinstance(source_doc_ids, list) or not source_doc_ids:
            raise ValueError(f'Module #{index} must reference at least one source_doc_id.')

        normalized_source_doc_ids = []
        seen_source_doc_ids = set()
        for source_doc_id in source_doc_ids:
            try:
                parsed_doc_id = int(source_doc_id)
            except (TypeError, ValueError) as exc:
                raise ValueError(f'Module #{index} has an invalid source_doc_id.') from exc

            if parsed_doc_id not in valid_doc_ids_set:
                raise ValueError(f'Module #{index} referenced unexpected source_doc_id {parsed_doc_id}.')
            if parsed_doc_id not in seen_source_doc_ids:
                normalized_source_doc_ids.append(parsed_doc_id)
                seen_source_doc_ids.add(parsed_doc_id)

        validated_modules.append({
            'sequence_order': sequence_order,
            'title': title,
            'content': content,
            'key_takeaway': key_takeaway,
            'reading_time_minutes': TARGET_READING_TIME_MINUTES,
            'source_doc_ids': normalized_source_doc_ids,
        })

    total_modules = payload.get('total_modules', len(validated_modules))
    try:
        total_modules = int(total_modules)
    except (TypeError, ValueError) as exc:
        raise ValueError('The AI response has an invalid total_modules value.') from exc

    if total_modules != len(validated_modules):
        raise ValueError('The AI response total_modules does not match the module list.')

    return {
        'document_summary': _normalize_whitespace(payload.get('document_summary', '')),
        'domains': normalized_domains,
        'total_modules': total_modules,
        'modules': validated_modules,
    }


def _summarize_documents_mock(documents, domain_names, custom_prompt=''):
    prefer_cjk = any(_contains_cjk(document.get('raw_text', '')) for document in documents)
    focus_phrase = _build_focus_phrase(custom_prompt, prefer_cjk)
    per_document = []

    for document in documents:
        raw_text = document.get('raw_text', '')
        key_points = _select_key_sentences(raw_text, max_items=3)
        if not key_points:
            key_points = [_truncate_text(raw_text or document['file_name'], 120)]

        summary = _truncate_text(' '.join(key_points), 220)
        per_document.append({
            'doc_id': int(document['doc_id']),
            'file_name': document['file_name'],
            'summary': summary,
            'key_points': key_points[:3],
        })

    file_names = ', '.join(document['file_name'] for document in documents[:4])
    if prefer_cjk:
        batch_summary = (
            f'這批文件涵蓋 {file_names}，重點集中在 {focus_phrase}，'
            '可先用整體摘要掌握共同主題，再延伸成跨文件的微課模組。'
        )
    else:
        batch_summary = (
            f'This document batch covers {file_names} and focuses on {focus_phrase}. '
            'Use the shared summary first, then turn it into integrated micro-learning modules.'
        )

    return {
        'batch_summary': _truncate_text(batch_summary, 260),
        'documents': per_document,
    }


def _build_mock_module_content(group, domain_names, focus_phrase, prefer_cjk):
    summary_lead = group[0]['summary']
    point_one = group[0]['key_points'][0]
    extra_points = []
    for item in group:
        for point in item.get('key_points', []):
            if point != point_one and point not in extra_points:
                extra_points.append(point)

    if prefer_cjk:
        lines = [
            f'情境整理：本模組整合 {", ".join(item["file_name"] for item in group)} 的共同重點。',
            f'核心內容：{point_one}',
            f'應用方式：請從 {", ".join(domain_names)} 的角度，整理成可執行的作業或客戶溝通步驟。',
            f'延伸提醒：生成內容時持續強調 {focus_phrase}。',
        ]
        if extra_points:
            lines.insert(2, f'補充觀察：{extra_points[0]}')
    else:
        lines = [
            f'Scenario: This module synthesizes the shared ideas from {", ".join(item["file_name"] for item in group)}.',
            f'Core idea: {point_one}',
            f'Application: Reframe the material for {", ".join(domain_names)} and turn it into clear next-step guidance.',
            f'Focus: Keep emphasizing {focus_phrase}.',
        ]
        if extra_points:
            lines.insert(2, f'Additional signal: {extra_points[0]}')

    return _truncate_text(' '.join(lines), 900)


def _generate_batch_modules_mock(summary_payload, documents, domain_names, custom_prompt=''):
    prefer_cjk = any(_contains_cjk(document.get('raw_text', '')) for document in documents)
    focus_phrase = _build_focus_phrase(custom_prompt, prefer_cjk)
    profile = _profile_for(domain_names)
    summaries = summary_payload['documents']

    group_size = 2 if len(summaries) > 1 else 1
    groups = [
        summaries[index:index + group_size]
        for index in range(0, len(summaries), group_size)
    ][:4]

    modules = []
    for index, group in enumerate(groups, start=1):
        source_doc_ids = [int(item['doc_id']) for item in group]
        title_context = _truncate_text(group[0]['summary'], 30 if prefer_cjk else 42)
        if prefer_cjk:
            title = f'{profile["title_zh"]} {index}: {title_context}'
            key_takeaway = (
                f'{profile["takeaway_zh"]} '
                f'對應標籤：{", ".join(domain_names)}。'
                f'提示重點：{focus_phrase}。'
            )
        else:
            title = f'{profile["title_en"]} {index}: {title_context}'
            key_takeaway = (
                f'{profile["takeaway_en"]} '
                f'Domains applied: {", ".join(domain_names)}. '
                f'Focus: {focus_phrase}.'
            )

        modules.append({
            'sequence_order': index,
            'title': _truncate_text(title, 160),
            'content': _build_mock_module_content(group, domain_names, focus_phrase, prefer_cjk),
            'key_takeaway': _truncate_text(key_takeaway, 260),
            'reading_time_minutes': TARGET_READING_TIME_MINUTES,
            'source_doc_ids': source_doc_ids,
        })

    return {
        'document_summary': summary_payload['batch_summary'],
        'domains': domain_names,
        'total_modules': len(modules),
        'modules': modules,
    }


def generate_batch_micro_modules(documents, domain_names, custom_prompt=''):
    if not documents:
        raise ValueError('At least one source document is required.')
    if not domain_names:
        raise ValueError('At least one domain must be selected.')

    valid_doc_ids = [int(document['doc_id']) for document in documents]

    if _is_mock_llm_enabled():
        summary_payload = _summarize_documents_mock(documents, domain_names, custom_prompt=custom_prompt)
        generation_payload = _generate_batch_modules_mock(
            summary_payload,
            documents,
            domain_names,
            custom_prompt=custom_prompt,
        )
        return validate_micro_modules_payload(generation_payload, domain_names, valid_doc_ids)

    api_key = os.environ.get('OPENAI_API_KEY')
    if not api_key:
        raise LLMConfigurationError('OPENAI_API_KEY environment variable is not set.')

    model = os.environ.get('OPENAI_MODEL', DEFAULT_MODEL).strip() or DEFAULT_MODEL

    stage_one_prompt = _build_stage_one_prompt(documents, domain_names, custom_prompt=custom_prompt)
    summary_payload = _request_structured_output(
        api_key,
        model,
        stage_one_prompt,
        'batch_document_summaries',
        SUMMARY_SCHEMA,
    )
    validated_summary_payload = _validate_batch_summary_payload(summary_payload, documents)

    stage_two_prompt = _build_stage_two_prompt(
        validated_summary_payload,
        domain_names,
        custom_prompt=custom_prompt,
    )
    generation_payload = _request_structured_output(
        api_key,
        model,
        stage_two_prompt,
        'integrated_micro_modules',
        MODULES_SCHEMA,
    )
    validated_payload = validate_micro_modules_payload(
        generation_payload,
        domain_names,
        valid_doc_ids,
    )

    if not validated_payload['document_summary']:
        validated_payload['document_summary'] = validated_summary_payload['batch_summary']

    return validated_payload
