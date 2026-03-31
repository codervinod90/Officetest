import re
from collections import Counter


def execute(params: dict) -> dict:
    text = params.get("text", "")
    if not text.strip():
        return {"error": "text is required"}

    top_n = params.get("top_n", 5)

    words = re.findall(r'\b\w+\b', text.lower())
    sentences = [s.strip() for s in re.split(r'[.!?]+', text) if s.strip()]
    word_freq = Counter(words).most_common(top_n)

    avg_word_len = round(sum(len(w) for w in words) / len(words), 1) if words else 0

    return {
        "word_count": len(words),
        "char_count": len(text),
        "sentence_count": len(sentences),
        "avg_word_length": avg_word_len,
        "top_words": [{"word": w, "count": c} for w, c in word_freq],
        "unique_words": len(set(words)),
    }
