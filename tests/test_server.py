"""The server over its real stdio protocol, as Claude Code starts it."""

import asyncio
import json
import sys

import mcp
import pytest
from mcp.client.stdio import StdioServerParameters, stdio_client

import layouts

EXPECTED_TOOLS = {
    "list_stackups", "inspect_stackup", "inspect_gds", "check_layout_complexity", "measure_holes",
    "simplify_layout", "suggest_mesh_size", "check_ports", "propose_ports", "add_ports", "check_model_script",
    "generate_model", "wait_for_model", "check_repeated_patterns", "show_in_gmsh", "preview_layout", "summarize_results", "sparameter_summary",
    "plot_sparameters", "show_sparameters", "passive_values", "fit_equivalent_circuit",
}


def run(coroutine):
    return asyncio.run(asyncio.wait_for(coroutine, timeout=180))


async def session_calls(calls):
    params = StdioServerParameters(command=sys.executable, args=["-m", "gds2palace_mcp.server"])
    async with stdio_client(params) as (read, write):
        async with mcp.ClientSession(read, write) as session:
            init = await session.initialize()
            return init, await calls(session)


@pytest.mark.slow
def test_tools_resources_prompt_and_errors_over_stdio(tmp_path, line_gds, stackup_name):
    rl = layouts.series_network(tmp_path / "rl.s2p", "rl")

    async def calls(session):
        out = {}
        out["tools"] = {t.name: t for t in (await session.list_tools()).tools}
        out["prompts"] = [p.name for p in (await session.list_prompts()).prompts]
        out["resource"] = await session.read_resource("gds2palace://stackups/README.md")
        out["stackup"] = await session.call_tool("inspect_stackup", {"stackup_file": stackup_name})
        out["bad_stackup"] = await session.call_tool("inspect_stackup", {"stackup_file": "nonexistent.xml"})
        out["ports"] = await session.call_tool("check_ports", {
            "gds_file": line_gds, "stackup_file": stackup_name,
            "ports_list": [{"portnumber": 1, "source_layernum": 201, "from_layername": "Metal1",
                            "to_layername": "TopMetal2", "direction": "z"}]})
        out["preview"] = await session.call_tool("preview_layout", {"gds_file": line_gds, "ports": {"1": 201}})
        out["no_image"] = await session.call_tool("preview_layout", {"gds_file": line_gds, "send_image": False})
        out["values"] = await session.call_tool("passive_values", {"file": rl, "connection": "series"})
        return out

    init, out = run(session_calls(calls))
    assert init.server_info.name == "gds2palace"
    assert set(out["tools"]) == EXPECTED_TOOLS
    assert all(t.description and t.description.strip() for t in out["tools"].values())
    assert out["prompts"] == ["new_model"]
    assert "IHP stackup files" in out["resource"].contents[0].text
    assert out["stackup"].is_error is False
    # expected failures reach the client with their message instead of a bare "error"
    assert out["bad_stackup"].is_error is True and "SG13G2_FEM_200um.xml" in out["bad_stackup"].content[0].text
    assert json.loads(out["ports"].content[0].text)["ok"] is True
    assert [c.type for c in out["preview"].content] == ["image", "text"]
    assert out["no_image"].is_error is True and "needs save_png" in out["no_image"].content[0].text
    assert json.loads(out["values"].content[0].text)["L_low_frequency_nH"] == pytest.approx(1.0, rel=1e-3)
