from rhino_mcp.guidance import TOPICS, modeling_guidance


def test_packaged_guidance_topics_are_readable() -> None:
    for topic in TOPICS:
        result = modeling_guidance(topic)

        assert result["topic"] == topic
        assert result["available_topics"] == list(TOPICS)
        assert (
            result["package_version"] == "0+unknown"
            or result["package_version"] == "0.1.2"
        )
        assert result["content"].strip()
