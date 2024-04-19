"""
This file contains portions of code backported from the upcoming Django 5.1 release.
Specifically from these PRs:

 * https://github.com/django/django/pull/14824
 * https://github.com/django/django/pull/17760

Copied code is subject to the following license:

    Copyright (c) Django Software Foundation and individual contributors.
    All rights reserved.

    Redistribution and use in source and binary forms, with or without modification,
    are permitted provided that the following conditions are met:

        1. Redistributions of source code must retain the above copyright notice,
           this list of conditions and the following disclaimer.

        2. Redistributions in binary form must reproduce the above copyright
           notice, this list of conditions and the following disclaimer in the
           documentation and/or other materials provided with the distribution.

        3. Neither the name of Django nor the names of its contributors may be used
           to endorse or promote products derived from this software without
           specific prior written permission.

    THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
    ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
    WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
    DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR
    ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
    (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
    LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON
    ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
    (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
    SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
"""

import warnings

import django
from django.core.exceptions import ImproperlyConfigured
from django.db.backends.sqlite3.base import DatabaseWrapper as DjangoDatabaseWrapper


class ExtendedDatabaseWrapper(DjangoDatabaseWrapper):
    transaction_modes = frozenset(["DEFERRED", "EXCLUSIVE", "IMMEDIATE"])

    def get_connection_params(self):
        kwargs = super().get_connection_params()

        # Handle `init_command`
        init_command = kwargs.pop("init_command", "")
        self.init_commands = init_command.split(";")

        # Handle `transaction_mode`
        transaction_mode = kwargs.pop("transaction_mode", None)
        if (
            transaction_mode is not None
            and transaction_mode.upper() not in self.transaction_modes
        ):  # pragma: no cover
            allowed_transaction_modes = ", ".join(
                [f"{mode!r}" for mode in sorted(self.transaction_modes)]
            )
            raise ImproperlyConfigured(
                f"settings.DATABASES[{self.alias!r}]['OPTIONS']['transaction_mode'] "
                f"is improperly configured to '{transaction_mode}'. Use one of "
                f"{allowed_transaction_modes}, or None."
            )
        self.transaction_mode = transaction_mode.upper() if transaction_mode else None

        return kwargs

    def get_new_connection(self, conn_params):
        conn = super().get_new_connection(conn_params)
        for init_command in self.init_commands:
            if init_command := init_command.strip():
                conn.execute(init_command)
        return conn

    def _start_transaction_under_autocommit(self):
        if self.transaction_mode is None:  # pragma: no cover
            self.cursor().execute("BEGIN")
        else:
            self.cursor().execute(f"BEGIN {self.transaction_mode}")


if django.VERSION[:2] < (5, 1):
    DatabaseWrapper = ExtendedDatabaseWrapper
else:  # pragma: no cover
    warnings.warn(
        f"Django 5.1 backport disabled as Django {django.__version__} "
        f"detected; it should now be removed."
    )
    DatabaseWrapper = DjangoDatabaseWrapper
