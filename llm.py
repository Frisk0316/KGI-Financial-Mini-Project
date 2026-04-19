import re


def validate_micro_modules_payload(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise ValueError('Generated module data must be a JSON-like object.')

    modules = payload.get('modules')
    if not isinstance(modules, list) or not modules:
        raise ValueError('Generated module data must include at least one module.')

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
        raise ValueError('Generated module data has an invalid total_modules value.') from exc

    if total_modules != len(validated_modules):
        raise ValueError('Generated module data total_modules does not match the module list.')

    return {
        'document_summary': summary,
        'domains': payload.get('domains', []),
        'total_modules': total_modules,
        'modules': validated_modules,
    }


def _clean_text(text: str) -> str:
    text = re.sub(r'\r\n?', '\n', text)
    text = re.sub(r'[^\S\n]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _split_paragraphs(text: str) -> list[str]:
    paragraphs = [part.strip() for part in re.split(r'\n{2,}', _clean_text(text)) if part.strip()]
    return paragraphs


def _split_sentences(text: str) -> list[str]:
    normalized = re.sub(r'\s+', ' ', text).strip()
    if not normalized:
        return []

    parts = re.split(r'(?<=[。！？!?\.])\s+', normalized)
    sentences = [part.strip() for part in parts if part.strip()]
    return sentences or [normalized]


def _measure_text_units(text: str) -> int:
    english_words = len(re.findall(r'[A-Za-z0-9]+', text))
    cjk_chars = len(re.findall(r'[\u4e00-\u9fff]', text))
    other_chars = len(re.sub(r'\s+', '', text)) - cjk_chars
    return max(english_words + cjk_chars + max(other_chars // 6, 0), 1)


def _chunk_paragraphs(paragraphs: list[str], target_units: int = 180, max_units: int = 260) -> list[str]:
    if not paragraphs:
        return []

    chunks = []
    current_parts = []
    current_units = 0

    for paragraph in paragraphs:
        paragraph_units = _measure_text_units(paragraph)
        would_exceed = current_parts and current_units + paragraph_units > max_units
        close_enough = current_parts and current_units >= target_units

        if would_exceed or close_enough:
            chunks.append('\n\n'.join(current_parts))
            current_parts = []
            current_units = 0

        current_parts.append(paragraph)
        current_units += paragraph_units

    if current_parts:
        chunks.append('\n\n'.join(current_parts))

    return chunks


def _truncate_text(text: str, max_length: int) -> str:
    text = re.sub(r'\s+', ' ', text).strip()
    if len(text) <= max_length:
        return text
    return text[: max_length - 3].rstrip() + '...'


def _build_document_summary(sentences: list[str], domain_names: list[str]) -> str:
    focus = ', '.join(domain_names)
    if not sentences:
        return f'This module set highlights the selected domains: {focus}.'

    summary_seed = ' '.join(sentences[:2])
    summary_seed = _truncate_text(summary_seed, 180)
    return f'{summary_seed} Focus areas: {focus}.'


def _build_title(chunk_text: str, domain_names: list[str], index: int) -> str:
    first_sentence = _split_sentences(chunk_text)[0] if _split_sentences(chunk_text) else ''
    seed = re.sub(r'^[\W_]+', '', first_sentence)
    seed = _truncate_text(seed, 42)
    primary_domain = domain_names[0] if domain_names else f'Module {index}'

    if not seed:
        return f'{primary_domain} Sprint {index}'
    return f'{primary_domain}: {seed}'


def _build_takeaway(chunk_text: str, domain_names: list[str]) -> str:
    first_sentence = _split_sentences(chunk_text)[0] if _split_sentences(chunk_text) else ''
    focus = ', '.join(domain_names)
    takeaway_seed = _truncate_text(first_sentence, 110) if first_sentence else 'Review the main points in this section.'
    return f'Apply this section with focus on {focus}: {takeaway_seed}'


def _estimate_reading_time_minutes(text: str) -> float:
    units = _measure_text_units(text)
    minutes = units / 140
    return round(min(max(minutes, 1.0), 3.0), 1)


def generate_micro_modules(raw_text: str, domain_names: list[str]) -> dict:
    cleaned_text = _clean_text(raw_text)
    paragraphs = _split_paragraphs(cleaned_text)
    if not paragraphs:
        raise ValueError('Source text does not contain enough content to build modules.')

    chunks = _chunk_paragraphs(paragraphs)
    sentences = _split_sentences(cleaned_text)
    modules = []

    for index, chunk in enumerate(chunks, start=1):
        content = f"Relevant domains: {', '.join(domain_names)}.\n\n{chunk}".strip()
        modules.append({
            'sequence_order': index,
            'title': _build_title(chunk, domain_names, index),
            'content': content,
            'key_takeaway': _build_takeaway(chunk, domain_names),
            'reading_time_minutes': _estimate_reading_time_minutes(content),
        })

    return validate_micro_modules_payload({
        'document_summary': _build_document_summary(sentences, domain_names),
        'domains': domain_names,
        'total_modules': len(modules),
        'modules': modules,
    })
