from pathlib import Path

import pytest
from pytestarch import LayeredArchitecture, LayerRule, get_evaluable_architecture

from distributed_smb.application.protocols import (
    DiscoveryServiceProtocol,
    GameEventBrokerProtocol,
    LobbyServiceProtocol,
    NoopDiscoveryService,
    NoopGameEventBroker,
    NoopLobbyService,
    NoopRecoveryProber,
    RecoveryProberProtocol,
)
from distributed_smb.application.reconciliation import (
    NoopPredictionEngine,
    NoopShadowCopy,
    PredictionEngineProtocol,
    ShadowCopyProtocol,
)

PACKAGE = Path(__file__).resolve().parents[1] / "src" / "distributed_smb"


def _application_modules_except_the_dto() -> list[str]:
    """Listed from the directory, so a new module joins the layer on its own."""
    modules = []
    for path in sorted((PACKAGE / "application").iterdir()):
        if path.name.startswith("__") or path.name == "dto.py":
            continue
        if path.suffix == ".py" or (path / "__init__.py").exists():
            modules.append(f"distributed_smb.application.{path.stem}")
    return modules


@pytest.fixture(scope="module")
def code():
    """The real import graph of the package, parsed once for this module."""
    return get_evaluable_architecture(str(PACKAGE), str(PACKAGE))


@pytest.fixture(scope="module")
def layers():
    return (
        LayeredArchitecture()
        .layer("domain")
        .containing_modules("distributed_smb.domain")
        .layer("agents")
        .containing_modules("distributed_smb.agents")
        .layer("network")
        .containing_modules("distributed_smb.network")
        .layer("application")
        .containing_modules(_application_modules_except_the_dto())
        .layer("dto")
        .containing_modules("distributed_smb.application.dto")
        .layer("presentation")
        .containing_modules("distributed_smb.presentation")
        .layer("shared")
        .containing_modules("distributed_smb.shared")
    )


def _forbid(layers, source: str, forbidden: list[str]) -> LayerRule:
    return (
        LayerRule()
        .based_on(layers)
        .layers_that()
        .are_named(source)
        .should_not()
        .access_layers_that()
        .are_named(forbidden)
    )


def test_domain_depends_on_no_other_layer(code, layers):
    """The game rules must stay playable without any networking at all."""
    others = ["agents", "network", "application", "dto", "presentation"]
    _forbid(layers, "domain", others).assert_applies(code)


def test_agents_depend_only_on_shared_definitions(code, layers):
    """Agents use observation/action contracts, never game or infrastructure internals."""
    _forbid(
        layers,
        "agents",
        ["domain", "network", "application", "dto", "presentation"],
    ).assert_applies(code)


def test_network_does_not_depend_on_application_or_presentation(code, layers):
    _forbid(layers, "network", ["application", "dto", "presentation"]).assert_applies(
        code
    )


def test_application_may_integrate_agents_without_reverse_dependencies(code, layers):
    """The integration belongs to application; agent code stays independent of it."""
    _forbid(layers, "agents", ["application", "dto"]).assert_applies(code)


def test_application_does_not_depend_on_presentation(code, layers):
    """Renderer and InputHandler are injected by main.py, never imported."""
    _forbid(layers, "application", ["presentation"]).assert_applies(code)
    _forbid(layers, "dto", ["presentation"]).assert_applies(code)


def test_presentation_takes_nothing_but_the_dto(code, layers):
    """The renderer draws a RenderFrame and knows nothing else."""
    _forbid(
        layers, "presentation", ["domain", "network", "application", "agents"]
    ).assert_applies(code)


def test_shared_definitions_are_independent_of_implementation_layers(code, layers):
    _forbid(
        layers,
        "shared",
        ["domain", "agents", "application", "dto", "network", "presentation"],
    ).assert_applies(code)


def test_every_noop_stub_satisfies_the_protocol_it_stands_in_for():
    """The stubs are the defaults a node boots with: if one drifts from its
    protocol, injection silently breaks at the layer boundary."""
    stubs = [
        (NoopGameEventBroker(), GameEventBrokerProtocol),
        (NoopLobbyService(), LobbyServiceProtocol),
        (NoopDiscoveryService(), DiscoveryServiceProtocol),
        (NoopRecoveryProber(), RecoveryProberProtocol),
        (NoopPredictionEngine(), PredictionEngineProtocol),
        (NoopShadowCopy(), ShadowCopyProtocol),
    ]
    for stub, protocol in stubs:
        assert isinstance(stub, protocol), type(stub).__name__
