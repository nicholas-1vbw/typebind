"""Check actual Literal specialization and downstream strict diagnostics."""

import json
import subprocess
import sys
from pathlib import Path

SOURCE = """\
from typing import Annotated, Any, Literal, assert_type
from pydantic import BaseModel, TypeAdapter
from typebind import DiscriminatedModel, bind_variants, build_adapter, tag_field
from typebind import DiscoveredVariant, discover_variants, select_variants


class Message[Kind: str](DiscriminatedModel):
    kind: Kind = tag_field()
    identifier: str

class Request[Method: str](Message[Literal["request"]]):
    method: Method = tag_field()
    sequence: int

type QueryTag = Literal["query"]

class Query(Request[QueryTag]):
    expression: str

class Write(Request[Literal["write"]]):
    value: int

class Batch[Mode: str](Request[Literal["batch"]]):
    mode: Mode = tag_field()

class Stream(Batch[Literal["stream"]]):
    cursor: str

Ping = Message[Literal["ping"]]
binding = bind_variants([Query, Write, Stream, Ping])
type MessageValue = Annotated[Message[Any], binding]
type RequestValue = Annotated[Request[Any], binding]

class AliasedDocument(BaseModel):
    primary: MessageValue
    requests: list[RequestValue]

class Document(BaseModel):
    primary: Query | Write
    requests: list[Query | Write | Stream]

class OpenDocument(BaseModel):
    primary: Annotated[Message[Any], bind_variants([Query, Write, Stream])]
    requests: list[Annotated[Request[Any], bind_variants([Query, Write, Stream])]]

query = Query(identifier="q", sequence=1, expression="x")
assert_type(query.kind, Literal["request"])
assert_type(query.method, Literal["query"])
assert_type(query.identifier, str)
assert_type(query.sequence, int)
assert_type(query.expression, str)
stream = Stream(identifier="s", sequence=2, cursor="x")
assert_type(stream.kind, Literal["request"])
assert_type(stream.method, Literal["batch"])
assert_type(stream.mode, Literal["stream"])

ping = Ping(identifier="p")
assert_type(ping.kind, Literal["ping"])
assert_type(ping.identifier, str)
aliased_document = AliasedDocument(primary=ping, requests=[query, stream])
assert_type(aliased_document.primary.identifier, str)
assert_type(aliased_document.requests[0].sequence, int)
alias_adapter = TypeAdapter(AliasedDocument)
assert_type(alias_adapter.validate_json(b'{}'), AliasedDocument)

def takes_request(value: Request[Literal["query"]]) -> None:
    assert_type(value.method, Literal["query"])

takes_request(query)
def get_method[T: str](request: Request[T]) -> T:
    return request.method

assert_type(get_method(query), Literal["query"])
assert_type(get_method(stream), Literal["batch"])
document = Document(primary=query, requests=[query, stream])
adapter = build_adapter(Message[Any], variants=[Query, Write, Stream])
assert_type(adapter, TypeAdapter[Message[Any]])
assert_type(adapter.validate_json(b'{}'), Message[Any])
discovered = discover_variants(
    groups=(group for group in ["example.extensions"]), include=lambda entry: entry.name != "x"
)
assert_type(discovered, tuple[DiscoveredVariant, ...])
selected = select_variants(
    discovered, family=Message[Any], groups=iter(["example.extensions"]),
    extra_variants=[Query, Stream],
)
assert_type(selected, tuple[type[DiscriminatedModel], ...])
for family in (DiscriminatedModel, Query, Request[Literal["query"]]):
    assert_type(select_variants(discovered, family=family), tuple[type[DiscriminatedModel], ...])
discovered_adapter = build_adapter(Message[Any], variants=selected)
assert_type(discovered_adapter, TypeAdapter[Message[Any]])
assert_type(discovered_adapter.validate_json(b'{}').identifier, str)
discovered_binding = bind_variants(selected)

class DiscoveredEnvelope(BaseModel):
    requests: list[Annotated[Request[Any], discovered_binding]]

assert_type(DiscoveredEnvelope(requests=[query]).requests[0].sequence, int)
envelope = TypeAdapter(Document)
assert_type(envelope.validate_python({}), Document)
open_adapter = TypeAdapter(OpenDocument)
open_document = open_adapter.validate_python({})
assert_type(open_document.primary.identifier, str)
assert_type(open_document.requests[0].sequence, int)

def takes_string_request(request: Request[str]) -> None:
    assert_type(request.method, str)

def narrowed(value: Query | Write) -> None:
    if value.method == "query":
        assert_type(value, Query)
    else:
        assert_type(value, Write)
"""

# Every line must be rejected. These are data for a separate Pyright invocation,
# not unchecked declarations in the package itself.
INVALID = """\
Query(identifier="q", sequence="wrong", expression="x")
Query(identifier="q", expression="x")
Query(identifier="q", sequence=1, expression="x", kind="wrong")
Query(identifier="q", sequence=1, expression="x", method="write")
query.kind = "wrong"
query.method = "wrong"
query.expression = 123
query.missing
class RawString(Message["request"]): pass
class InvalidType(Message[int]): pass
class NumericTag(Message[Literal[1]]): pass
class TooMany(Message[Literal["a"], Literal["b"]]): pass
takes_request(Write(identifier="w", sequence=1, value=2))
Stream(identifier="s", sequence=1, cursor="x", mode="other")
takes_string_request(query)
build_adapter(Document, variants=[Query])
open_document.primary.identifier = 123
open_document.requests[0].sequence = "wrong"
Ping(identifier="p", kind="wrong")
Ping()
ping.kind = "wrong"
aliased_document.primary.identifier = 123
aliased_document.requests[0].sequence = "wrong"
select_variants(discovered, family=Document)
select_variants(discovered, family=Message[Any], extra_variants=[Document])
select_variants(discovered, family=Message[Any], extra_variants=[query])
discover_variants(groups={1})
DiscoveredVariant(Document, discovered[0].entry_point)
"""


def test_strict_downstream_typing(tmp_path: Path) -> None:
    (tmp_path / "pyrightconfig.json").write_text(
        json.dumps({"typeCheckingMode": "strict", "pythonVersion": "3.14"})
    )
    sample = tmp_path / "consumer.py"
    sample.write_text(SOURCE)
    command = [
        sys.executable,
        "-m",
        "pyright",
        "--project",
        str(tmp_path),
        "--pythonpath",
        sys.executable,
        "--outputjson",
    ]
    positive = subprocess.run(command, capture_output=True, text=True, check=False)
    assert positive.returncode == 0, positive.stdout + positive.stderr
    assert json.loads(positive.stdout)["summary"]["errorCount"] == 0
    sample.write_text(SOURCE + INVALID)
    negative = subprocess.run(command, capture_output=True, text=True, check=False)
    assert negative.returncode != 0
    diagnostics = json.loads(negative.stdout)["generalDiagnostics"]
    errors = [item for item in diagnostics if item["severity"] == "error"]
    first = len(SOURCE.splitlines())
    for line in range(first, first + len(INVALID.splitlines())):
        assert any(item["range"]["start"]["line"] == line for item in errors), (line, errors)
    assert all(item["range"]["start"]["line"] >= first for item in errors)
