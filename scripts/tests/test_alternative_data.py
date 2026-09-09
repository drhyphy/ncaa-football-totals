"""Small provenance/parser checks; these tests do not download or fit models."""
import hashlib
import importlib.util
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]


def module(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


fetch = module("fetch_alternative_data")
normalize = module("normalize_alternative_supplements")


def test_sdql_top_level_commas_preserve_unquoted_arrays():
    assert normalize.split('20190824, [14, 14, 7, 10], "A, B", , HAW') == [
        '20190824', '[14, 14, 7, 10]', '"A, B"', '', 'HAW'
    ]


@pytest.mark.parametrize('row', ['a,[1,2,b', 'a,1],b', 'a,"unfinished,b'])
def test_sdql_rejects_unbalanced_rows(row):
    with pytest.raises(ValueError):
        normalize.split(row)


def test_downloader_rejects_corrupt_response_before_write(tmp_path):
    class Response:
        content = b'corrupt'
        headers = {}
        def raise_for_status(self):
            pass
    class Session:
        def get(self, *args, **kwargs):
            return Response()
    path = tmp_path / 'quotes.json'
    with pytest.raises(ValueError):
        fetch.download(Session(), 'https://example.org/public.json', path, hashlib.sha256(b'expected').hexdigest())
    assert not path.exists()
    assert not path.with_name('quotes.json.download').exists()


def test_verified_cache_needs_no_network(tmp_path):
    class Session:
        def get(self, *args, **kwargs):
            raise AssertionError('Verified cached source should not trigger network')
    path = tmp_path / 'quotes.json'
    path.write_bytes(b'fixed public data')
    result = fetch.download(Session(), 'https://example.org/public.json', path, hashlib.sha256(path.read_bytes()).hexdigest())
    assert result['cached'] is True
    assert result['sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()


def test_all_market_sources_have_immutable_pins_and_hashes():
    for _, url, digest in fetch.PINNED_FILES:
        assert len(digest) == 64
        assert '/main/' not in url and '/master/' not in url
        assert '/raw.githubusercontent.com/' in url
