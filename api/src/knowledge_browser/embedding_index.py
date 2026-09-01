import re


def sentences(text):
    return [part.strip() for part in re.findall(r"[^.!?]+[.!?]?", text) if part.strip()]


def collect_sentences(documents):
    values = []
    for document in documents:
        for field, texts in document.fields.items():
            if field == "issue_metadata":
                continue
            for text in filter(None, texts):
                values.extend(sentences(text))
    return list(dict.fromkeys(values))


def create_embeddings(client, texts, model, *, batch_size=100):
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    unique, result = list(dict.fromkeys(texts)), {}
    for start in range(0, len(unique), batch_size):
        batch = unique[start:start + batch_size]
        response = client.embeddings.create(model=model, input=batch)
        items = response.data
        if len(items) != len(batch) or len({item.index for item in items}) != len(items):
            raise ValueError("embedding provider returned invalid indexes")
        by_index = {item.index: item.embedding for item in items}
        if set(by_index) != set(range(len(batch))):
            raise ValueError("embedding provider returned invalid indexes")
        if any(len(vector) != 1536 for vector in by_index.values()):
            raise ValueError("embedding provider returned invalid dimensions")
        result.update({text: by_index[index] for index, text in enumerate(batch)})
    return result
