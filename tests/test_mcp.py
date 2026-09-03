import os
import unittest
from urllib.parse import parse_qs, urlparse

from mcp.client import Client
from mcp.client.stdio import StdioServerParameters, stdio_client
from pydantic import ValidationError

from geekbi_shein_mcp import __version__
from geekbi_shein_mcp.auth import ActionRequired, AuthManager, VERSION_HEADER, default_auth_path
from geekbi_shein_mcp.client import GeekBIClient
from geekbi_shein_mcp.config import PLATFORM_SLUG, RESULT_WINDOW, TOOL_SPECS
from geekbi_shein_mcp.models import MODEL_CLASSES
from geekbi_shein_mcp.server import _execute, mcp


EXPECTED_TOOLS = {tool["name"] for tool in TOOL_SPECS}


class StubAuth:
    def __init__(self):
        self.urls = []

    def request_json(self, url, timeout, **kwargs):
        self.urls.append(url)
        return {"code": 0, "data": {"total": 0, "list": [], "site": {"siteId": 1}}}


class ContractTests(unittest.TestCase):
    def test_every_parameter_has_description(self):
        for model in MODEL_CLASSES.values():
            with self.subTest(model=model.__name__):
                self.assertEqual([], [name for name, field in model.model_fields.items() if not field.description])

    def test_range_validation(self):
        model = next((m for m in MODEL_CLASSES.values() if "priceMin" in m.model_fields), None)
        if model is None:
            self.skipTest("platform has no price range")
        with self.assertRaises(ValidationError):
            model(priceMin=100, priceMax=10)

    def test_result_window_validation(self):
        model = next((m for m in MODEL_CLASSES.values() if "page" in m.model_fields), None)
        if model is None:
            self.skipTest("platform has no paged tool")
        size = model.model_fields["size"].metadata[1].le if len(model.model_fields["size"].metadata) > 1 else 20
        with self.assertRaises(ValidationError):
            model(page=RESULT_WINDOW + 1, size=1)

    def test_query_serialization(self):
        key, model = next((key, model) for key, model in MODEL_CLASSES.items() if "page" in model.model_fields)
        query = model()
        client = GeekBIClient(base_url="https://example.test", timeout=1)
        auth = StubAuth()
        client.auth = auth
        endpoint = next(tool["endpoint"] for tool in TOOL_SPECS if tool["key"] == key)
        client.query(endpoint, query, "测试查询")
        params = parse_qs(urlparse(auth.urls[0]).query)
        self.assertEqual(["1"], params["page"])

    def test_action_required_is_structured(self):
        def requires_action():
            raise ActionRequired("需要登录后继续", "https://example.test/login", action="AUTH_REQUIRED")
        result = _execute(requires_action)
        self.assertTrue(result["actionRequired"])
        self.assertNotIn("deviceCode", result)

    def test_auth_storage_and_version_header(self):
        self.assertEqual(f"{PLATFORM_SLUG}-research-mcp", default_auth_path().parent.name)
        headers = AuthManager("https://example.test")._headers()
        self.assertEqual(__version__, headers[VERSION_HEADER])

    def test_required_one_of_rule(self):
        model = next(
            (model for model in MODEL_CLASSES.values() if getattr(model, "__required_one_of__", ())),
            None,
        )
        if model is None:
            self.skipTest("platform has no one-of lookup")
        with self.assertRaises(ValidationError):
            model()


class ServerTests(unittest.IsolatedAsyncioTestCase):
    async def test_registered_tools(self):
        tools = await mcp.list_tools()
        self.assertEqual(EXPECTED_TOOLS, {tool.name for tool in tools})
        self.assertTrue(all(tool.output_schema for tool in tools))
        self.assertEqual([], await mcp.list_resource_templates())
        self.assertEqual([], await mcp.list_prompts())

    async def test_stdio_discovery(self):
        parameters = StdioServerParameters(
            command=os.path.abspath(".venv/bin/python"),
            args=["-m", "geekbi_shein_mcp"],
            cwd=os.getcwd(),
        )
        async with Client(stdio_client(parameters), mode="legacy") as client:
            tools = await client.list_tools()
            self.assertEqual(EXPECTED_TOOLS, {tool.name for tool in tools.tools})


if __name__ == "__main__":
    unittest.main()
