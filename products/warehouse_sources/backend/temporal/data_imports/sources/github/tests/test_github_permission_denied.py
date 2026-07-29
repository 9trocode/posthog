import pytest
from unittest import mock

import requests

from products.warehouse_sources.backend.temporal.data_imports.sources.github import github


def _forbidden_response() -> mock.Mock:
    response = mock.Mock(spec=requests.Response)
    response.status_code = 403
    response.ok = False
    response.headers = {}
    response.text = '{"message": "Resource not accessible by personal access token"}'
    response.json.return_value = {"message": "Resource not accessible by personal access token"}
    response.request = None
    response.raise_for_status.side_effect = requests.exceptions.HTTPError(
        "403 Client Error: Forbidden for url", response=response
    )
    return response


def _no_resume() -> mock.Mock:
    manager = mock.Mock()
    manager.can_resume.return_value = False
    return manager


@pytest.mark.parametrize(
    "endpoint",
    [
        # Walks /repos/{repo}/deployments directly.
        "deployments",
        # Fans out from the same parent list, so the denial lands inside the parent walk.
        "deployment_statuses",
    ],
)
def test_missing_endpoint_grant_syncs_zero_rows_and_names_the_grant(endpoint: str) -> None:
    # A token without `deployments: read` used to escape as an HTTPError that hard-failed the whole
    # schema and surfaced to the user as a message-less non-retryable failure.
    session = mock.Mock()
    session.request.return_value = _forbidden_response()
    logger = mock.Mock()

    with mock.patch.object(github, "make_tracked_session", return_value=session):
        rows = list(
            github.get_rows(
                personal_access_token="tok",
                repository="acme/widgets",
                endpoint=endpoint,
                logger=logger,
                resumable_source_manager=_no_resume(),
            )
        )

    assert rows == []
    # One denied request, then stop — the grant gap is repo-wide, so re-requesting is pure waste.
    assert session.request.call_count == 1
    warning = " ".join(str(call) for call in logger.warning.call_args_list)
    assert "Deployments: read" in warning
