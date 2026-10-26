from html import escape


def preview_response(display_name: str) -> dict:
    return {
        "statusCode": 200,
        "headers": {"Content-Type": "text/html; charset=utf-8"},
        "body": f"<h1>Delivery preview for {escape(display_name)}</h1>",
    }
