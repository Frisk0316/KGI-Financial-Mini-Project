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

THEME_RULES = [
    {
        'name': 'Compliance',
        'keywords': ['compliance', 'aml', 'kyc', 'fatca', 'crs', '法遵', '合規', '內控', '洗錢', '申報', '揭露'],
        'titles': {'zh': '法遵要求與作業重點', 'en': 'Compliance Requirements and Operating Controls'},
        'takeaways': {
            'zh': '先確認法規責任、內控節點與揭露要求，再安排後續作業。',
            'en': 'Clarify the regulatory duties, control points, and disclosure requirements before proceeding.',
        },
    },
    {
        'name': 'CRM',
        'keywords': ['crm', 'client', 'customer', 'service', 'follow-up', 'follow up', '客戶', '服務', '溝通', '追蹤'],
        'titles': {'zh': '客戶服務與溝通重點', 'en': 'Client Communication and Service Priorities'},
        'takeaways': {
            'zh': '把制度要求轉成客戶可理解的溝通與服務行動。',
            'en': 'Translate policy requirements into actions that improve client communication and service.',
        },
    },
    {
        'name': 'TaxRegulations',
        'keywords': ['tax', 'filing', 'withholding', 'cfc', '稅', '稅務', '課稅', '扣繳', '報稅'],
        'titles': {'zh': '稅務規則與申報提醒', 'en': 'Tax Rules and Filing Reminders'},
        'takeaways': {
            'zh': '先釐清課稅基礎、申報時點與適用規則，再安排下一步。',
            'en': 'Identify the tax basis, filing timing, and applicable rules before planning next steps.',
        },
    },
    {
        'name': 'LifeInsurance',
        'keywords': ['life insurance', 'policy', 'beneficiary', 'coverage', 'premium', '保單', '壽險', '保障', '受益人'],
        'titles': {'zh': '壽險保障與保單重點', 'en': 'Life Insurance Coverage and Policy Essentials'},
        'takeaways': {
            'zh': '從保障內容、保單義務與客戶需求三個面向說明產品。',
            'en': 'Frame the product through coverage, policy obligations, and client needs.',
        },
    },
    {
        'name': 'InvestmentLinked',
        'keywords': ['investment', 'fund', 'portfolio', 'allocation', 'nav', 'etf', '投資', '基金', '配置', '標的'],
        'titles': {'zh': '投資型商品與配置重點', 'en': 'Investment-Linked Product and Allocation Essentials'},
        'takeaways': {
            'zh': '先理解商品結構、標的配置與風險報酬，再討論投資安排。',
            'en': 'Understand the product structure, underlying assets, and risk-return profile before discussing allocation.',
        },
    },
    {
        'name': 'WealthManagement',
        'keywords': ['wealth', 'asset', 'portfolio', 'succession', 'planning', 'estate', '財富', '資產', '傳承', '信託'],
        'titles': {'zh': '財富管理與資產規劃重點', 'en': 'Wealth Management and Asset Planning Priorities'},
        'takeaways': {
            'zh': '把商品配置、融資安排與傳承規劃放回客戶整體資產目標中思考。',
            'en': 'Position products, financing, and succession planning within the client broader asset goals.',
        },
    },
    {
        'name': 'Other',
        'keywords': ['general', 'overview', 'process', 'workflow', 'training', '其他', '通用', '流程', '作業', '訓練'],
        'titles': {'zh': '其他主題與通用作業重點', 'en': 'General Topics and Operational Highlights'},
        'takeaways': {
            'zh': '當內容不屬於既有分類時，先整理成可落地的共通作業與學習重點。',
            'en': 'When the material does not fit a predefined domain, distill it into practical shared operating guidance.',
        },
    },
]

SYSTEM_PROMPT = (
    'You are an expert instructional designer for a financial services micro-learning platform. '
    'Transform source material into concise, accurate, job-relevant learning sprints. '
    'Return valid JSON only and strictly follow the requested schema.'
)

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


def _read_positive_int_env(name: str, default: int) -> int:
    raw_value = os.environ.get(name, '').strip()
    if not raw_value:
        return default

    try:
        parsed = int(raw_value)
    except ValueError:
        return default

    return parsed if parsed > 0 else default


def _is_mock_llm_enabled() -> bool:
    return os.environ.get('MOCK_LLM', '').strip().lower() in MOCK_LLM_ENABLED_VALUES


def _normalize_whitespace(text: str) -> str:
    return re.sub(r'\s+', ' ', text or '').strip()


def _truncate_text(text: str, limit: int) -> str:
    text = _normalize_whitespace(text)
    if len(text) <= limit:
        return text
    return f'{text[:limit - 3].rstrip()}...'


def _contains_cjk(text: str) -> bool:
    return bool(re.search(r'[\u3400-\u9fff]', text or ''))


def _split_sentences(text: str) -> list[str]:
    parts = re.split(r'(?<=[.!?。！？])\s+|\n+', text or '')
    return [part.strip() for part in parts if part.strip()]


def _detect_themes(text: str, domain_names: list[str]) -> list[str]:
    lowered = (text or '').lower()
    matched = []
    for rule in THEME_RULES:
        keyword_hits = sum(1 for keyword in rule['keywords'] if keyword.lower() in lowered)
        domain_bonus = 2 if rule['name'] in domain_names else 0
        score = keyword_hits + domain_bonus
        if score > 0:
            matched.append((score, rule['name']))
    matched.sort(reverse=True)
    return [name for _, name in matched]


def _get_theme_rule(theme_name: str) -> dict | None:
    for rule in THEME_RULES:
        if rule['name'] == theme_name:
            return rule
    return None


def _build_focus_phrase(custom_prompt: str, prefer_cjk: bool) -> str:
    cleaned = _truncate_text(custom_prompt, 60 if prefer_cjk else 80).strip(' .。；;：:')
    if cleaned:
        return cleaned
    return '實務應用與對客溝通' if prefer_cjk else 'practical application and client communication'


def _select_key_sentences(text: str, prefer_cjk: bool, max_items: int = 3) -> list[str]:
    seen = set()
    selected = []
    for sentence in _split_sentences(text):
        cleaned = _truncate_text(sentence.strip(' -•'), 120 if prefer_cjk else 160)
        if len(cleaned) < 8:
            continue
        signature = cleaned.lower()
        if signature in seen:
            continue
        seen.add(signature)
        selected.append(cleaned)
        if len(selected) >= max_items:
            return selected

    for fragment in re.split(r'[，,、;；]', text or ''):
        cleaned = _truncate_text(fragment.strip(' -•,，、'), 80 if prefer_cjk else 120)
        if len(cleaned) < 8:
            continue
        signature = cleaned.lower()
        if signature in seen:
            continue
        seen.add(signature)
        selected.append(cleaned)
        if len(selected) >= max_items:
            break

    return selected


def _create_openai_client(api_key: str):
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise LLMConfigurationError(
            'The openai package is not installed. Run "pip install -r requirements.txt".'
        ) from exc

    return OpenAI(api_key=api_key)


def _is_retryable_llm_error(exc: Exception) -> bool:
    status_code = getattr(exc, 'status_code', None)
    if status_code in RETRYABLE_STATUS_CODES:
        return True

    message = str(exc).lower()
    markers = (
        'connection error',
        'timed out',
        'timeout',
        'temporarily unavailable',
        'rate limit',
        'rate_limit_exceeded',
        'server error',
    )
    return any(marker in message for marker in markers)


def _extract_retry_delay_seconds(exc: Exception) -> float | None:
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


def _calculate_retry_delay_seconds(exc: Exception, attempt: int) -> float:
    explicit_delay = _extract_retry_delay_seconds(exc)
    if explicit_delay is not None:
        return explicit_delay

    message = str(exc).lower()
    status_code = getattr(exc, 'status_code', None)
    if status_code == 429 or 'rate limit' in message or 'rate_limit_exceeded' in message:
        return DEFAULT_RATE_LIMIT_DELAY_SECONDS

    return min(DEFAULT_TRANSIENT_DELAY_SECONDS * attempt, 10.0)


def _request_structured_output(api_key: str, model: str, prompt: str, schema_name: str, schema: dict) -> dict:
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


def _build_document_corpus(documents: list[dict]) -> str:
    pieces = []
    remaining = MAX_TOTAL_PROMPT_CHARS
    for document in documents:
        if remaining <= 0:
            break
        text_limit = min(MAX_DOC_PROMPT_CHARS, remaining)
        excerpt = _truncate_text(document.get('raw_text', ''), text_limit)
        block = (
            f"[Document {document['doc_id']}] {document['file_name']}\n"
            f"{excerpt}"
        )
        pieces.append(block)
        remaining -= len(excerpt)
    return '\n\n'.join(pieces)


def _build_stage_one_prompt(documents: list[dict], domain_names: list[str], custom_prompt: str = '') -> str:
    domains_str = ', '.join(domain_names)
    custom_prompt_block = (
        f"\nAdditional user instructions:\n{custom_prompt.strip()}\n"
        if custom_prompt.strip()
        else ''
    )
    return (
        f"You are analyzing a batch of related training documents for the domains: {domains_str}.\n\n"
        "Read all provided documents first, then produce:\n"
        "1. one combined batch summary\n"
        "2. one concise summary per document\n"
        "3. 2-4 key points per document\n"
        "Return the exact doc_id and file_name values that appear in the source blocks.\n"
        f"{custom_prompt_block}\n"
        "Source documents:\n"
        f"{_build_document_corpus(documents)}"
    )


def _validate_batch_summary_payload(payload: dict, documents: list[dict]) -> dict:
    if not isinstance(payload, dict):
        raise ValueError('The AI summary response must be a JSON object.')

    batch_summary = _normalize_whitespace(payload.get('batch_summary', ''))
    summaries = payload.get('documents')
    if not isinstance(summaries, list) or not summaries:
        raise ValueError('The AI summary response must include one summary per document.')

    expected_doc_ids = {int(document['doc_id']) for document in documents}
    validated_summaries = []
    seen_doc_ids = set()
    for summary in summaries:
        if not isinstance(summary, dict):
            raise ValueError('Each document summary must be an object.')
        doc_id = summary.get('doc_id')
        file_name = _normalize_whitespace(summary.get('file_name', ''))
        summary_text = _normalize_whitespace(summary.get('summary', ''))
        key_points = summary.get('key_points', [])

        try:
            doc_id = int(doc_id)
        except (TypeError, ValueError) as exc:
            raise ValueError('Each document summary must include a valid doc_id.') from exc

        if doc_id not in expected_doc_ids:
            raise ValueError(f'The AI summary response referenced unexpected doc_id {doc_id}.')
        if doc_id in seen_doc_ids:
            raise ValueError(f'The AI summary response duplicated doc_id {doc_id}.')
        if not file_name or not summary_text:
            raise ValueError(f'The AI summary response is missing content for doc_id {doc_id}.')
        if not isinstance(key_points, list):
            raise ValueError(f'The AI summary response key_points must be an array for doc_id {doc_id}.')

        seen_doc_ids.add(doc_id)
        validated_summaries.append({
            'doc_id': doc_id,
            'file_name': file_name,
            'summary': summary_text,
            'key_points': [_normalize_whitespace(point) for point in key_points if _normalize_whitespace(point)],
        })

    if seen_doc_ids != expected_doc_ids:
        raise ValueError('The AI summary response did not return all requested documents.')

    return {'batch_summary': batch_summary, 'documents': validated_summaries}


def _build_stage_two_prompt(summary_payload: dict, domain_names: list[str], custom_prompt: str = '') -> str:
    domains_str = ', '.join(domain_names)
    summaries_json = json.dumps(summary_payload, ensure_ascii=False, indent=2)
    custom_prompt_block = (
        f"\nAdditional user instructions:\n{custom_prompt.strip()}\n"
        if custom_prompt.strip()
        else ''
    )
    return (
        f"You are creating integrated micro-learning content for the domains: {domains_str}.\n\n"
        "Use the multi-document summaries below to generate a set of 2-minute learning sprints.\n"
        "Requirements:\n"
        "- Synthesize across related documents when useful.\n"
        "- Keep the selected domains exactly as provided.\n"
        "- Return source_doc_ids for every module so the backend can trace the source documents.\n"
        "- Each module must target about 2 minutes of reading time and keep reading_time_minutes within 1-3.\n"
        "- Keep the output practical, concise, and job-relevant.\n"
        f"{custom_prompt_block}\n"
        "Batch summaries:\n"
        f"{summaries_json}"
    )


def validate_micro_modules_payload(payload: dict, expected_domains: list[str], valid_doc_ids: list[int]) -> dict:
    if not isinstance(payload, dict):
        raise ValueError('The AI response must be a JSON object.')

    modules = payload.get('modules')
    if not isinstance(modules, list) or not modules:
        raise ValueError('The AI response must include at least one module.')

    validated_modules = []
    seen_sequence_orders = set()
    valid_doc_ids_set = {int(doc_id) for doc_id in valid_doc_ids}
    for index, module in enumerate(modules, start=1):
        if not isinstance(module, dict):
            raise ValueError(f'Module #{index} must be an object.')

        title = _normalize_whitespace(module.get('title', ''))
        content = _normalize_whitespace(module.get('content', ''))
        key_takeaway = _normalize_whitespace(module.get('key_takeaway', ''))
        sequence_order = module.get('sequence_order', index)
        reading_time = module.get('reading_time_minutes', 2)
        source_doc_ids = module.get('source_doc_ids', [])

        if not title:
            raise ValueError(f'Module #{index} is missing a title.')
        if not content:
            raise ValueError(f'Module #{index} is missing content.')

        try:
            sequence_order = int(sequence_order)
        except (TypeError, ValueError) as exc:
            raise ValueError(f'Module #{index} has an invalid sequence_order.') from exc

        if sequence_order <= 0:
            raise ValueError(f'Module #{index} must use a positive sequence_order.')
        if sequence_order in seen_sequence_orders:
            raise ValueError(f'Module #{index} reuses sequence_order {sequence_order}.')
        seen_sequence_orders.add(sequence_order)

        try:
            reading_time = float(reading_time)
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
        for doc_id in source_doc_ids:
            try:
                parsed_doc_id = int(doc_id)
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

    summary = _normalize_whitespace(payload.get('document_summary', ''))
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

    normalized_domains = [_normalize_whitespace(domain) for domain in returned_domains]
    if normalized_domains != expected_domains:
        raise ValueError('The AI response domains do not match the selected domains.')

    return {
        'document_summary': summary,
        'domains': normalized_domains,
        'total_modules': total_modules,
        'modules': validated_modules,
    }


def _summarize_documents_mock(documents: list[dict], domain_names: list[str], custom_prompt: str = '') -> dict:
    prefer_cjk = any(_contains_cjk(document.get('raw_text', '')) for document in documents)
    focus_phrase = _build_focus_phrase(custom_prompt, prefer_cjk)
    summaries = []
    for document in documents:
        text = _normalize_whitespace(document.get('raw_text', ''))
        sentences = _select_key_sentences(text, _contains_cjk(text), max_items=3)
        summary_text = _truncate_text(' '.join(sentences) or text, 180)
        summaries.append({
            'doc_id': int(document['doc_id']),
            'file_name': document['file_name'],
            'summary': summary_text,
            'key_points': sentences[:3],
        })

    doc_names = ', '.join(document['file_name'] for document in documents[:4])
    if prefer_cjk:
        batch_summary = (
            f'這是一組彼此相關的文件批次，涵蓋 {doc_names} 等內容，'
            f'並以 {focus_phrase} 為整合重點。'
        )
    else:
        batch_summary = (
            f'This is a related document batch covering {doc_names} and it emphasizes {focus_phrase}.'
        )

    return {'batch_summary': _truncate_text(batch_summary, 240), 'documents': summaries}


def _generate_batch_modules_mock(summary_payload: dict, documents: list[dict], domain_names: list[str], custom_prompt: str = '') -> dict:
    prefer_cjk = any(_contains_cjk(document.get('raw_text', '')) for document in documents)
    focus_phrase = _build_focus_phrase(custom_prompt, prefer_cjk)
    themes = _detect_themes(summary_payload['batch_summary'], domain_names)
    primary_theme = themes[0] if themes else (domain_names[0] if domain_names else 'Other')
    theme_rule = _get_theme_rule(primary_theme) or _get_theme_rule('Other')
    docs_by_id = {int(document['doc_id']): document for document in documents}
    document_summaries = summary_payload['documents']

    modules = []
    max_group_size = 2 if len(document_summaries) > 1 else 1
    groups = [
        document_summaries[index:index + max_group_size]
        for index in range(0, len(document_summaries), max_group_size)
    ][:4]

    for index, group in enumerate(groups, start=1):
        source_doc_ids = [int(item['doc_id']) for item in group]
        source_files = '、'.join(item['file_name'] for item in group) if prefer_cjk else ', '.join(item['file_name'] for item in group)
        combined_points = []
        for item in group:
            combined_points.extend(item.get('key_points', []))
        combined_points = combined_points[:3]

        if prefer_cjk:
            content_parts = [
                f'情境：本模組整合 {source_files} 的重點內容。',
                f'重點：{combined_points[0] if combined_points else item["summary"]}',
                f'應用：請從 {"、".join(domain_names)} 的角度，整理成可執行的學習卡，並聚焦 {focus_phrase}。',
            ]
            if len(combined_points) > 1:
                content_parts.insert(2, f'補充：{combined_points[1]}')
            title_context = _truncate_text(group[0]['summary'], 20)
        else:
            content_parts = [
                f'Scenario: This module combines the key themes from {source_files}.',
                f'Key point: {combined_points[0] if combined_points else group[0]["summary"]}',
                f'Application: Reframe the material for {", ".join(domain_names)} with emphasis on {focus_phrase}.',
            ]
            if len(combined_points) > 1:
                content_parts.insert(2, f'Additional note: {combined_points[1]}')
            title_context = _truncate_text(group[0]['summary'], 28)

        base_title = theme_rule['titles']['zh' if prefer_cjk else 'en']
        title = f'{base_title}：{title_context}' if prefer_cjk else f'{base_title}: {title_context}'
        takeaway = theme_rule['takeaways']['zh' if prefer_cjk else 'en']
        if prefer_cjk:
            takeaway = f'{takeaway} 套用領域：{"、".join(domain_names)}。聚焦方向：{focus_phrase}。'
        else:
            takeaway = f'{takeaway} Domains applied: {", ".join(domain_names)}. Focus: {focus_phrase}.'

        modules.append({
            'sequence_order': index,
            'title': title,
            'content': _truncate_text(' '.join(content_parts), 900),
            'key_takeaway': _truncate_text(takeaway, 240),
            'reading_time_minutes': TARGET_READING_TIME_MINUTES,
            'source_doc_ids': source_doc_ids,
        })

    document_summary = summary_payload['batch_summary']
    return {
        'document_summary': document_summary,
        'domains': domain_names,
        'total_modules': len(modules),
        'modules': modules,
    }


def generate_batch_micro_modules(documents: list[dict], domain_names: list[str], custom_prompt: str = '') -> dict:
    if not documents:
        raise ValueError('At least one source document is required.')

    valid_doc_ids = [int(document['doc_id']) for document in documents]

    if _is_mock_llm_enabled():
        summary_payload = _summarize_documents_mock(documents, domain_names, custom_prompt=custom_prompt)
        payload = _generate_batch_modules_mock(summary_payload, documents, domain_names, custom_prompt=custom_prompt)
        return validate_micro_modules_payload(payload, domain_names, valid_doc_ids)

    api_key = os.environ.get('OPENAI_API_KEY')
    if not api_key:
        raise LLMConfigurationError('OPENAI_API_KEY environment variable is not set.')

    model = os.environ.get('OPENAI_MODEL', DEFAULT_MODEL).strip() or DEFAULT_MODEL

    summary_prompt = _build_stage_one_prompt(documents, domain_names, custom_prompt=custom_prompt)
    summary_payload = _request_structured_output(api_key, model, summary_prompt, 'batch_document_summaries', SUMMARY_SCHEMA)
    validated_summary_payload = _validate_batch_summary_payload(summary_payload, documents)

    generation_prompt = _build_stage_two_prompt(validated_summary_payload, domain_names, custom_prompt=custom_prompt)
    payload = _request_structured_output(api_key, model, generation_prompt, 'integrated_micro_modules', MODULES_SCHEMA)
    validated_payload = validate_micro_modules_payload(payload, domain_names, valid_doc_ids)
    if not validated_payload['document_summary']:
        validated_payload['document_summary'] = validated_summary_payload['batch_summary']
    return validated_payload
