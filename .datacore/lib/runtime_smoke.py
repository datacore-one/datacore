"""Disposable candidate qualification: no provider calls or production files."""
import asyncio
import base64
from contextlib import asynccontextmanager
import importlib.metadata as md
import json
import os
from pathlib import Path
import sys
import tempfile
import warnings

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / '.datacore/lib'))


async def main():
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    results = {}
    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter('always')
        from mcp.server.fastmcp import FastMCP
        transitions = []
        @asynccontextmanager
        async def lifespan(server):
            transitions.append('start')
            try:
                yield {'fixture': True}
            finally:
                transitions.append('stop')
        custom = FastMCP('fixture', lifespan=lifespan)
        async with custom._mcp_server.lifespan(custom._mcp_server) as context:
            assert context == {'fixture': True}
        assert transitions == ['start', 'stop']
        results['custom_lifespan'] = transitions
        results['warnings'] = [{'category': type(w.message).__name__, 'message': str(w.message)} for w in seen]
    with tempfile.TemporaryDirectory(prefix='datacore-candidate-') as tmp:
        base = Path(tmp)
        from PIL import Image, ImageDraw, ImageFont
        image = Image.new('RGB', (1400, 300), 'white')
        ImageDraw.Draw(image).text((30, 70), 'SAFE RUNTIME FIXTURE', fill='black',
                                   font=ImageFont.load_default(size=72))
        image_path = base / 'fixture.png'
        image.save(image_path)
        for restart in range(2):
            params = StdioServerParameters(command=sys.executable,
                args=[str(root / '.datacore/lib/ocr-server/server.py')], cwd=base,
                env={'HOME': str(base), 'DATACORE_ROOT': str(base),
                     'PATH': os.environ.get('PATH', os.defpath)})
            with (base / 'server.stderr').open('w') as err:
                async with stdio_client(params, errlog=err) as (read, write):
                    async with ClientSession(read, write) as client:
                        await client.initialize()
                        available = {t.name for t in (await client.list_tools()).tools}
                        assert 'extract_text_from_image' in available
                        result = await client.call_tool('extract_text_from_image', {'image_path': str(image_path)})
                        assert not result.isError
                        assert 'SAFE RUNTIME FIXTURE' in '\n'.join(getattr(x, 'text', '') for x in result.content)
            results[f'ocr_start_call_stop_{restart}'] = 'PASS'
        import click
        def forbidden(*a, **kw):
            raise AssertionError('vulnerable Click editor/pager path invoked')
        click.edit = click.echo_via_pager = forbidden
        from gtts import gTTS
        gTTS.save = gTTS.stream = forbidden
        import speech_transport as speech
        from urllib.parse import parse_qs
        requests = []
        def fake_post(url, body, **kwargs):
            assert url.startswith('https://translate.google.com/')
            rpc = json.loads(parse_qs(body.decode())['f.req'][0])
            assert rpc[0][0][0] == 'jQ1olc'
            requests.append(rpc)
            return json.dumps([['wrb.fr', 'jQ1olc', json.dumps([
                base64.b64encode(b'fixture mp3 bytes').decode()])]]).encode()
        speech.public_post = fake_post
        target = base / 'speech.mp3'
        target.write_bytes(b'previous')
        speech.synthesize_google('Synthetic runtime fixture.', target, allow_cloud=True)
        assert target.read_bytes() == b'fixture mp3 bytes' and requests
        results['real_gtts_formatter_without_unsafe_transport_or_click_editor'] = 'PASS'
    results['versions'] = {name: md.version(name) for name in ('mcp', 'pydantic', 'pydantic-settings', 'gtts', 'click')}
    print(json.dumps(results, indent=2))


if __name__ == '__main__':
    asyncio.run(asyncio.wait_for(main(), 45))
