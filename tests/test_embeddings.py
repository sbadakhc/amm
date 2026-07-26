import embeddings


def test_embed_text_returns_embedding_vector(fake_post):
    fake_post(embeddings, {"data": [{"embedding": [0.1, 0.2, 0.3]}]})

    result = embeddings.embed_text("Apple iPhone 16 Pro Max 256GB.")

    assert result == [0.1, 0.2, 0.3]
