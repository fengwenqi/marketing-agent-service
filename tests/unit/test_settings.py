from app.settings import Settings


def test_video_script_concurrency_defaults_to_three() -> None:
    assert Settings(_env_file=None).video_script_max_concurrency == 3
