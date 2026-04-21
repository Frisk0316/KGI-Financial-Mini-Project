import json
import os
import re


DEFAULT_MODEL = 'gpt-5.4-mini'
TARGET_READING_TIME_MINUTES = 2.0
MOCK_LLM_ENABLED_VALUES = {'1', 'true', 'yes', 'on'}
MOCK_MAX_MODULES = 4
MOCK_MIN_CHUNK_CHARS = 280
MOCK_TARGET_CHUNK_CHARS = 850

THEME_RULES = [
    {
        'name': 'Compliance',
        'keywords': [
            'compliance', 'aml', 'kyc', 'fatca', 'crs', '法令', '合規', '稽核', '申報', '盡職審查',
            '洗錢', '資恐', '公平待客', '內控', '違規', '揭露', '風險預告', '消費者保護',
        ],
        'titles': {
            'zh': '合規要求與作業重點',
            'en': 'Compliance Requirements and Operating Controls',
        },
        'takeaways': {
            'zh': '先釐清法規義務、控制點與對客戶的揭露要求，再執行後續流程。',
            'en': 'Clarify the regulatory duties, control points, and disclosure requirements before proceeding.',
        },
    },
    {
        'name': 'CRM',
        'keywords': [
            'crm', '客戶', '服務', '待客', '溝通', '申訴', '適合度', '業務人員', '客戶體驗',
            '理財業務', '關係', '顧問', '招攬',
        ],
        'titles': {
            'zh': '客戶溝通與服務重點',
            'en': 'Client Communication and Service Priorities',
        },
        'takeaways': {
            'zh': '把制度要求轉成對客戶可理解、可執行的服務與溝通動作。',
            'en': 'Translate policy requirements into actions that improve client communication and service.',
        },
    },
    {
        'name': 'TaxRegulations',
        'keywords': [
            'tax', '稅', '遺產稅', '贈與稅', '所得稅', 'cfc', '申報', '課稅', '資本利得',
            '節稅', '傳承稅務',
        ],
        'titles': {
            'zh': '稅務規範與申報提醒',
            'en': 'Tax Rules and Filing Reminders',
        },
        'takeaways': {
            'zh': '先辨識課稅基礎、申報時點與可適用規則，再進一步規劃。',
            'en': 'Identify the tax basis, filing timing, and applicable rules before planning next steps.',
        },
    },
    {
        'name': 'LifeInsurance',
        'keywords': [
            'life insurance', '人壽', '壽險', '保單', '保險', '身故', '祝壽', '解約', '年金',
            '終身壽險', '保費',
        ],
        'titles': {
            'zh': '壽險保障與保單重點',
            'en': 'Life Insurance Coverage and Policy Essentials',
        },
        'takeaways': {
            'zh': '從保障內容、保單權利義務與客戶需求三個面向解讀商品。',
            'en': 'Frame the product through coverage, policy obligations, and client needs.',
        },
    },
    {
        'name': 'InvestmentLinked',
        'keywords': [
            'investment', '基金', '投資型', '標的', '淨值', '配息', '債券', '股票', 'etf',
            '資產配置', '贖回', '單位數', '投資機構',
        ],
        'titles': {
            'zh': '投資型商品與配置重點',
            'en': 'Investment-Linked Product and Allocation Essentials',
        },
        'takeaways': {
            'zh': '先確認商品結構、投資標的與收益風險，再討論配置方式。',
            'en': 'Understand the product structure, underlying assets, and risk-return profile before discussing allocation.',
        },
    },
    {
        'name': 'WealthManagement',
        'keywords': [
            'wealth', '高資產', '財富管理', '傳承', '信託', '家族', '資產', '配置', '融資',
            '理財', '家族辦公室',
        ],
        'titles': {
            'zh': '財富管理與資產規劃重點',
            'en': 'Wealth Management and Asset Planning Priorities',
        },
        'takeaways': {
            'zh': '把產品、融資與傳承安排放回客戶整體資產目標下思考。',
            'en': 'Position products, financing, and succession planning within the client’s broader asset goals.',
        },
    },
]

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
    seen_sequence_orders = set()
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

        validated_modules.append({
            'sequence_order': sequence_order,
            'title': title,
            'content': content,
            'key_takeaway': key_takeaway,
            'reading_time_minutes': TARGET_READING_TIME_MINUTES,
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


def _build_user_prompt(raw_text: str, domain_names: list[str], custom_prompt: str = '') -> str:
    domains_str = ', '.join(domain_names)
    custom_prompt_block = (
        f"""

Additional user instructions:
{custom_prompt}
"""
        if custom_prompt.strip()
        else ''
    )
    return f"""You are creating training content for the domains: {domains_str}.

Create 2-minute learning sprints from the source text below.

Requirements:
- Keep the content faithful to the source text.
- Use clear, professional language for financial services staff.
- Emphasize practical application for the selected domains: {domains_str}.
- Do not expose personal data directly; generalize sensitive examples when necessary.
- Split the material into coherent modules.
- Each module should target about 2 minutes of reading time and report reading_time_minutes within 1-3.
- Produce concise titles and one key takeaway per module.
- Return the selected domains exactly as provided.
- If the source text is only loosely related to the selected domains, still generate faithful modules grounded in the text and treat the selected domains as contextual lenses instead of forcing a false match.
{custom_prompt_block}

Source text:
---
{raw_text}
---"""


def _is_mock_llm_enabled() -> bool:
    return os.environ.get('MOCK_LLM', '').strip().lower() in MOCK_LLM_ENABLED_VALUES


def _split_sentences(text: str) -> list[str]:
    parts = re.split(r'(?<=[。！？.!?])\s+|\n+', text)
    return [part.strip() for part in parts if part.strip()]


def _contains_cjk(text: str) -> bool:
    return bool(re.search(r'[\u3400-\u9fff]', text))


def _normalize_whitespace(text: str) -> str:
    return re.sub(r'\s+', ' ', text).strip()


def _is_noise_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return True
    if re.fullmatch(r'[-–—_/\\\d\s]+', stripped):
        return True
    if re.fullmatch(r'第?\s*\d+\s*頁(?:，?共?\d+\s*頁)?', stripped):
        return True
    if len(stripped) <= 3 and re.search(r'\d', stripped):
        return True
    return False


def _split_paragraphs(text: str) -> list[str]:
    raw_blocks = re.split(r'\n\s*\n+', text)
    paragraphs = []

    for block in raw_blocks:
        lines = [line.strip() for line in block.splitlines()]
        lines = [line for line in lines if not _is_noise_line(line)]
        if not lines:
            continue

        current = []
        for line in lines:
            heading_like = len(line) <= 26 and not line.endswith(('。', '.', '；', ';', '：', ':'))
            if heading_like and current:
                paragraphs.append(_normalize_whitespace(' '.join(current)))
                current = [line]
            else:
                current.append(line)
        if current:
            paragraphs.append(_normalize_whitespace(' '.join(current)))

    if paragraphs:
        return paragraphs

    sentences = _split_sentences(text)
    return [_normalize_whitespace(sentence) for sentence in sentences if sentence.strip()]


def _chunk_paragraphs(paragraphs: list[str], max_chunks: int = MOCK_MAX_MODULES) -> list[str]:
    if not paragraphs:
        return []

    chunks = []
    current = []
    current_len = 0

    for paragraph in paragraphs:
        paragraph = _normalize_whitespace(paragraph)
        if not paragraph:
            continue

        paragraph_len = len(paragraph)
        should_flush = (
            current
            and current_len >= MOCK_MIN_CHUNK_CHARS
            and (current_len + paragraph_len > MOCK_TARGET_CHUNK_CHARS or len(chunks) + 1 >= max_chunks)
        )

        if should_flush:
            chunks.append(' '.join(current).strip())
            current = []
            current_len = 0

        current.append(paragraph)
        current_len += paragraph_len

    if current:
        chunks.append(' '.join(current).strip())

    if len(chunks) > max_chunks:
        overflow = chunks[max_chunks - 1:]
        chunks = chunks[:max_chunks - 1] + [' '.join(overflow).strip()]

    return [chunk for chunk in chunks if chunk]


def _detect_chunk_themes(chunk: str, domain_names: list[str]) -> list[str]:
    lowered = chunk.lower()
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


def _extract_context_phrase(chunk: str, prefer_cjk: bool) -> str:
    sentences = _split_sentences(chunk)
    for sentence in sentences:
        cleaned = _normalize_whitespace(sentence).strip(' ,.;:：')
        if len(cleaned) < 12:
            continue
        return cleaned[:18] if prefer_cjk else cleaned[:28]
    return ''


def _make_title_unique(title: str, seen_titles: dict[str, int], prefer_cjk: bool) -> str:
    count = seen_titles.get(title, 0) + 1
    seen_titles[title] = count
    if count == 1:
        return title
    suffix = f'（第{count}節）' if prefer_cjk else f' (Part {count})'
    return f'{title}{suffix}'


def _build_mock_title(chunk: str, index: int, domain_names: list[str], seen_titles: dict[str, int]) -> str:
    normalized = _normalize_whitespace(chunk)
    if not normalized:
        return f'Sprint {index}'

    prefer_cjk = _contains_cjk(normalized)
    themes = _detect_chunk_themes(normalized, domain_names)
    primary_theme = themes[0] if themes else (domain_names[0] if domain_names else None)
    theme_rule = _get_theme_rule(primary_theme) if primary_theme else None

    if theme_rule:
        base_title = theme_rule['titles']['zh' if prefer_cjk else 'en']
    else:
        base_title = '文件重點與實務提醒' if prefer_cjk else 'Document Highlights and Practical Reminders'

    context_phrase = _extract_context_phrase(normalized, prefer_cjk)
    if context_phrase:
        if prefer_cjk:
            title = f'{base_title}：{context_phrase}'
        else:
            title = f'{base_title}: {context_phrase}'
    else:
        title = base_title

    return _make_title_unique(title, seen_titles, prefer_cjk)


def _build_mock_takeaway(chunk: str, domain_names: list[str]) -> str:
    normalized = _normalize_whitespace(chunk)
    prefer_cjk = _contains_cjk(normalized)
    themes = _detect_chunk_themes(normalized, domain_names)
    primary_theme = themes[0] if themes else (domain_names[0] if domain_names else None)
    theme_rule = _get_theme_rule(primary_theme) if primary_theme else None
    lead = _extract_context_phrase(normalized, prefer_cjk)
    domains_str = ', '.join(domain_names)

    if theme_rule:
        guidance = theme_rule['takeaways']['zh' if prefer_cjk else 'en']
    else:
        guidance = (
            '先整理原文重點，再轉成對客戶與作業流程都能落地的行動。'
            if prefer_cjk
            else 'Distill the source material into actions that teams can apply in client and operational settings.'
        )

    if prefer_cjk:
        if lead:
            return f'{lead}，{guidance} 本模組對應 domains：{domains_str}。'
        return f'{guidance} 本模組對應 domains：{domains_str}。'

    if lead:
        return f'{lead}. {guidance} Domains applied: {domains_str}.'
    return f'{guidance} Domains applied: {domains_str}.'


def _build_mock_summary(raw_text: str, domain_names: list[str], custom_prompt: str = '') -> str:
    paragraphs = _split_paragraphs(raw_text)
    summary_source = ' '.join(paragraphs[:2]) if paragraphs else raw_text
    summary_source = _normalize_whitespace(summary_source)
    prefer_cjk = _contains_cjk(summary_source)
    themes = _detect_chunk_themes(summary_source, domain_names)
    theme_phrase = ', '.join(themes[:2] or domain_names[:2])

    if prefer_cjk:
        summary = f'本文件聚焦於{theme_phrase}相關重點，適合整理為可執行的微學習模組。'
        if summary_source:
            summary = f'{summary} 內容摘要：{summary_source[:120]}'
        if custom_prompt.strip():
            summary = f'{summary} 額外指示：{custom_prompt.strip()[:80]}'
    else:
        summary = f'This document focuses on {theme_phrase} topics and is suitable for practical micro-learning modules.'
        if summary_source:
            summary = f'{summary} Summary seed: {summary_source[:120]}'
        if custom_prompt.strip():
            summary = f'{summary} Additional focus: {custom_prompt.strip()[:80]}'

    return summary[:280].strip()


def _chunk_sentences(sentences: list[str], target_chunks: int) -> list[str]:
    if not sentences:
        return []

    chunk_size = max(1, len(sentences) // max(1, target_chunks))
    chunks = []
    for index in range(0, len(sentences), chunk_size):
        chunk = ' '.join(sentences[index:index + chunk_size]).strip()
        if chunk:
            chunks.append(chunk)
    return chunks


def _generate_mock_payload(raw_text: str, domain_names: list[str], custom_prompt: str = '') -> dict:
    normalized_text = _normalize_whitespace(raw_text)
    paragraphs = _split_paragraphs(raw_text)
    chunks = _chunk_paragraphs(paragraphs, max_chunks=MOCK_MAX_MODULES)
    if not chunks:
        sentences = _split_sentences(raw_text)
        target_chunks = 3 if len(normalized_text) > 2500 else 2
        chunks = _chunk_sentences(sentences, target_chunks) or [normalized_text[:1200]]
        chunks = chunks[:MOCK_MAX_MODULES]

    summary = _build_mock_summary(raw_text, domain_names, custom_prompt=custom_prompt)

    modules = []
    seen_titles = {}
    for index, chunk in enumerate(chunks, start=1):
        content = _normalize_whitespace(chunk)[:900]
        modules.append({
            'sequence_order': index,
            'title': _build_mock_title(content, index, domain_names, seen_titles),
            'content': content,
            'key_takeaway': _build_mock_takeaway(content, domain_names),
            'reading_time_minutes': TARGET_READING_TIME_MINUTES,
        })

    return {
        'document_summary': summary,
        'domains': domain_names,
        'total_modules': len(modules),
        'modules': modules,
    }


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


def generate_micro_modules(raw_text: str, domain_names: list[str], custom_prompt: str = '') -> dict:
    if _is_mock_llm_enabled():
        payload = _generate_mock_payload(raw_text, domain_names, custom_prompt=custom_prompt)
        return validate_micro_modules_payload(payload)

    api_key = os.environ.get('OPENAI_API_KEY')
    if not api_key:
        raise LLMConfigurationError('OPENAI_API_KEY environment variable is not set.')

    model = os.environ.get('OPENAI_MODEL', DEFAULT_MODEL).strip() or DEFAULT_MODEL
    truncated_text = raw_text[:80_000]
    prompt = _build_user_prompt(truncated_text, domain_names, custom_prompt=custom_prompt)
    payload = _request_structured_output(api_key, model, prompt)
    validated = validate_micro_modules_payload(payload)

    if validated['domains'] != domain_names:
        raise ValueError('The AI response domains do not match the selected domains.')

    return validated
