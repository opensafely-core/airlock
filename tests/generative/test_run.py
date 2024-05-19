from hypothesis.stateful import rule, precondition, RuleBasedStateMachine
import hypothesis.strategies as st

import inspect
import json
from hashlib import file_digest
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest
from django.conf import settings

import old_api
from airlock.business_logic import (
    AuditEvent,
    AuditEventType,
    BusinessLogicLayer,
    CodeRepo,
    DataAccessLayerProtocol,
    FileReview,
    FileReviewStatus,
    RequestFileType,
    RequestStatus,
    UrlPath,
    Workspace,
    bll,
)
from tests import factories


# pytestmark = pytest.mark.django_db

filename_strategy = st.text(
    st.characters(max_codepoint=1000, blacklist_categories=('Cc', 'Cs')),
    min_size=1).map(lambda s: s.strip()).filter(lambda s: len(s) > 0)

class AirlockMachine(RuleBasedStateMachine):
    def __init__(self):
        super(AirlockMachine, self).__init__()
        self.author = factories.create_user(username="author", workspaces=["workspace"])
        self.workspace = factories.create_workspace("workspace")
        self.release_request = factories.create_release_request(
            "workspace", status=RequestStatus.PENDING, user=self.author
        )

    @rule(filename=filename_strategy)
    def add_file(self, filename):
        # assert False
        path = UrlPath(f"path/{filename}.txt")
        factories.write_workspace_file(self.workspace, path)
        bll.add_file_to_request(self.release_request, path, self.author)

    @rule()
    @precondition(lambda self: self.release_request.filegroups)
    def withdraw_first_file(self):
        first_filegroup_name, first_filegroup = self.release_request.filegroups.popitem()
        first_file = first_filegroup.files.popitem()
        # raise Exception(first_file)
        bll.withdraw_file_from_request(
            self.release_request,
            first_filegroup_name / first_file[0],
            user=self.author
        )

TestAirlockMachine = AirlockMachine.TestCase

if __name__ == "__main__":
    unittest.main()
