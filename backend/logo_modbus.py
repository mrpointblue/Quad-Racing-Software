from backend.services.logo_client import build_logo_client


def read_logo_status(settings: dict) -> dict:
    return build_logo_client(settings).read_status()


def test_logo_connection(settings: dict) -> dict:
    return build_logo_client(settings).test_connection()
