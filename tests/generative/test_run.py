import hypothesis.strategies as st
import pytest
from hypothesis import assume
from hypothesis.stateful import (
    RuleBasedStateMachine,
    initialize,
    invariant,
    precondition,
    rule,
    Bundle,
)

from airlock.business_logic import (
    RequestStatus,
    UrlPath,
    bll,
)
from local_db.models import (
    FileReview,
)
from tests import factories


# pytestmark = pytest.mark.django_db

# only lower-case ASCII
filename_strategy = st.text(
    st.characters(min_codepoint=97, max_codepoint=122), min_size=1
)


# Notes from chat with David MacIver:
#
# * preconditions for everything
# * separate methods for passing/failing calls
# * consider one big "bad state transition" method for all the transitions
# * as many verification methods as you like, they're not expensive
#
# TOASK:
# * how to do an OR list of preconditions -> use helper functions I guess
# *
#

## helper functions for preconditions


def request_pending(airlock_machine):
    return airlock_machine.release_request.status == RequestStatus.PENDING


def request_not_pending(airlock_machine):
    return not request_pending(airlock_machine)


def request_submitted(airlock_machine):
    return airlock_machine.release_request.status == RequestStatus.SUBMITTED


def has_filegroups(airlock_machine):
    if airlock_machine.release_request.filegroups:
        return True
    return False


def has_files(airlock_machine):
    if not has_filegroups(airlock_machine):
        return False
    return airlock_machine.release_request.filegroups["default"].files


def filegroups_have_c2(airlock_machine):
    if not has_filegroups(airlock_machine):
        return False
    # TODO: extend
    return airlock_machine.release_request.filegroups["default"].context != ""


class AirlockMachine(RuleBasedStateMachine):
    @initialize()
    def populate_db(self):
        self.author = factories.create_user(username="author", workspaces=["workspace"])
        self.checker1 = factories.create_user(username="checker1", output_checker=True)
        self.checker2 = factories.create_user(username="checker2", output_checker=True)
        self.workspace = factories.create_workspace("workspace")
        self.release_request = factories.create_release_request(
            "workspace", status=RequestStatus.PENDING, user=self.author
        )

    # TODO: tidy up filenames vs paths
    filenames = Bundle("filenames")

    # @rule(filename=st.just("a"))
    @rule(target=filenames, filename=filename_strategy)
    @precondition(request_pending)
    def add_file(self, filename):
        path = UrlPath(f"path/{filename}.txt")
        if self.release_request.filegroups:
            # TODO: does it back-track here or abandon?
            # TODO: this should check all filegroups
            assume(
                path
                not in list(self.release_request.filegroups.popitem()[1].files.keys())
            )
        factories.write_workspace_file(self.workspace, path)
        bll.add_file_to_request(self.release_request, path, self.author)
        self.release_request = factories.refresh_release_request(self.release_request)
        return filename

    @rule(filename=filename_strategy)
    @precondition(request_not_pending)
    def add_file_fail(self, filename):
        # TODO: this has removed a file (& group!)
        path = UrlPath(f"path/{filename}.txt")
        if self.release_request.filegroups:
            # TODO: does it back-track here or abandon?
            # TODO: this should check all filegroups
            assume(
                path
                not in list(self.release_request.filegroups["default"].files.keys())
            )
        # TODO: cleanup this file
        factories.write_workspace_file(self.workspace, path)
        with pytest.raises(bll.RequestPermissionDenied):
            bll.add_file_to_request(self.release_request, path, self.author)

    # # TODO: strategy to draw the filegroup to modify
    @rule(
        filegroup=st.just("default"),
        context=filename_strategy,
        controls=filename_strategy,
    )
    @precondition(has_filegroups)
    @precondition(request_pending)
    def update_c2(self, filegroup, context, controls):
        bll.group_edit(
            self.release_request,
            filegroup,
            context,
            controls,
            self.author,
        )
        self.release_request = factories.refresh_release_request(self.release_request)

    # # TODO: this is fixed in main
    # # TODO: strategy to draw the filegroup to modify
    # @rule(
    #     filegroup=st.just("default"),
    #     context=filename_strategy,
    #     controls=filename_strategy,
    # )
    # @precondition(has_filegroups)
    # @precondition(request_not_pending)
    # def update_c2_fail(self, filegroup, context, controls):
    #     # with pytest.raises(Exception):
    #     bll.group_edit(
    #         self.release_request,
    #         filegroup,
    #         context,
    #         controls,
    #         self.author,
    #     )

    @rule(filename=filenames)
    @precondition(has_files)
    @precondition(request_pending)
    def withdraw_file(self, filename):
        path = UrlPath(f"path/{filename}.txt")
        filegroup = "default"
        assume(
            path in list(self.release_request.filegroups["default"].files.keys())
        )
        bll.withdraw_file_from_request(
            self.release_request, filegroup / path, user=self.author
        )

    @rule(filename=filenames)
    @precondition(has_files)
    @precondition(request_pending)
    def withdraw_file_fail(self, filename):
        path = UrlPath(f"path/{filename}.txt")
        filegroup = "default"
        assume(
            path not in list(self.release_request.filegroups["default"].files.keys())
        )
        with pytest.raises(bll.FileNotFound):
            bll.withdraw_file_from_request(
                self.release_request, filegroup / path, user=self.author
            )

    @rule()
    @precondition(has_files)
    @precondition(filegroups_have_c2)
    @precondition(request_pending)
    def submit_request(self):
        bll.set_status(self.release_request, RequestStatus.SUBMITTED, user=self.author)

    @rule()
    @precondition(request_not_pending)
    def submit_request_fail(self):
        with pytest.raises(Exception):
            bll.set_status(
                self.release_request, RequestStatus.SUBMITTED, user=self.author
            )

    # select a file from the Bundle
    @rule(filename=filenames)
    @precondition(request_submitted)
    def review_file(self, filename):
        # TODO: this fails because this can be any filename
        path = UrlPath(f"path/{filename}.txt")
        assume(
            path in list(self.release_request.filegroups["default"].files.keys())
        )
        request_file = self.release_request.get_request_file_from_output_path(path)
        bll.approve_file(self.release_request, request_file, self.checker1)
        bll.set_status(
            self.release_request, RequestStatus.PARTIALLY_REVIEWED, user=self.checker1
        )

    #     bll.set_status(self.release_request, RequestStatus.SUBMITTED, user=self.author1)

    # @rule()
    # @precondition(request_submitted)
    # def approve_request(self):
    #     bll.set_status(release_request, RequestStatus.APPROVED, user=self.checker1)

    @invariant()
    @precondition(request_submitted)
    def at_least_one_file(self):
        try:
            assert len(self.release_request.filegroups["default"].files) > 0
        except KeyError:
            raise Exception(self.release_request.filegroups)

    # @invariant()
    # def no_reviews(self):
    #     for filegroup in self.release_request.filegroups:
    #         for file in self.release_request.filegroups[filegroup].files:
    #             for review in (
    #                 self.release_request.filegroups[filegroup].files[file].reviews
    #             ):
    #                 assert review == False

    def teardown(self):
        # # this is just for cleanup, so reach around the bll
        # # & forcibly delete the request
        if hasattr(self, "release_request"):
            # manually delete FileReview objects, because they don't cascade delete
            FileReview.objects.filter().delete()
            bll._dal._delete_release_request(self.release_request.id)


TestAirlockMachine = AirlockMachine.TestCase

if __name__ == "__main__":
    unittest.main()
