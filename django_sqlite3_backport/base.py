"""
This file contains portions of code backported from the upcoming Django 5.1 release.
Specifically from these PRs:

 * https://github.com/django/django/pull/14824

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
from django.db.backends.sqlite3.base import DatabaseWrapper as DjangoDatabaseWrapper


class ExtendedDatabaseWrapper(DjangoDatabaseWrapper):
    def get_connection_params(self):
        kwargs = super().get_connection_params()
        init_command = kwargs.pop("init_command", "")
        self.init_commands = init_command.split(";")
        return kwargs

    def get_new_connection(self, conn_params):
        conn = super().get_new_connection(conn_params)
        for init_command in self.init_commands:
            if init_command := init_command.strip():
                conn.execute(init_command)
        return conn


if django.VERSION[:2] < (5, 1):
    DatabaseWrapper = ExtendedDatabaseWrapper
else:  # pragma: no cover
    warnings.warn(
        f"Django 5.1 backport disabled as Django {django.__version__} "
        f"detected; it should now be removed."
    )
    DatabaseWrapper = DjangoDatabaseWrapper
