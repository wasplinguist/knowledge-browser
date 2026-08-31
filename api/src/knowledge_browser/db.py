from collections.abc import Iterator
from contextlib import contextmanager
import re

import psycopg

from .config import database_url


_SQL_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


@contextmanager
def connection() -> Iterator[psycopg.Connection]:
    with psycopg.connect(database_url()) as conn:
        yield conn


def allowed_document_sql(
    user_parameter: str = "user_id", document_alias: str = "documents"
) -> str:
    if not _SQL_IDENTIFIER.fullmatch(user_parameter) or not _SQL_IDENTIFIER.fullmatch(
        document_alias
    ):
        raise ValueError("ACL SQL identifier is invalid")

    parameter = f"%({user_parameter})s"
    return f"""
      EXISTS (
        SELECT 1 FROM users acl_user
        WHERE acl_user.id = {parameter}
      )
      AND EXISTS (
        SELECT 1
        FROM permission_sets acl_permission_set
        WHERE acl_permission_set.id = {document_alias}.permission_set_id
          AND (
            acl_permission_set.visibility = 'company'
            OR EXISTS (
              SELECT 1
              FROM permission_set_users acl_user_link
              WHERE acl_user_link.permission_set_id = acl_permission_set.id
                AND acl_user_link.user_id = {parameter}
            )
            OR EXISTS (
              SELECT 1
              FROM permission_set_groups acl_group_link
              JOIN group_memberships acl_membership
                ON acl_membership.group_id = acl_group_link.group_id
              WHERE acl_group_link.permission_set_id = acl_permission_set.id
                AND acl_membership.user_id = {parameter}
            )
          )
      )
    """
