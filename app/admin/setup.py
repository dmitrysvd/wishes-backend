from fastapi import Request
from sqladmin import Admin, ModelView
from sqladmin.authentication import AuthenticationBackend

from app.config import settings
from app.db import User, Wish


class UserAdmin(ModelView, model=User):
    column_list = [User.id, User.display_name, User.registered_at]
    icon = "fa-solid fa-user"
    column_searchable_list = [User.display_name, User.id]
    column_default_sort = ('registered_at', True)
    column_details_exclude_list = [User.vk_access_token, User.firebase_push_token]
    form_excluded_columns = [User.vk_access_token, User.firebase_push_token]
    can_export = False


class WishAdmin(ModelView, model=Wish):
    name_plural = 'Wishes'
    column_list = [Wish.id, Wish.name, Wish.user, Wish.created_at]
    icon = "fa-solid fa-gift"
    column_searchable_list = [Wish.name, User.id]
    column_default_sort = ('created_at', True)
    can_export = False


class AdminAuth(AuthenticationBackend):
    async def login(self, request: Request) -> bool:
        form = await request.form()
        username, password = form["username"], form["password"]
        if (
            username == 'admin'
            and settings.ADMIN_PASSWORD
            and password == settings.ADMIN_PASSWORD
        ):
            request.session.update({"has_admin_access": True})
            return True
        else:
            return False

    async def logout(self, request: Request) -> bool:
        request.session.clear()
        return True

    async def authenticate(self, request: Request) -> bool:
        has_admin_access = request.session.get("has_admin_access", False)
        return has_admin_access


def setup_admin(app, engine):
    """Настройка админ-панели SQLAdmin."""
    admin = Admin(
        app,
        engine,
        authentication_backend=AdminAuth(secret_key=settings.SECRET_KEY),
    )
    admin.add_view(UserAdmin)
    admin.add_view(WishAdmin)
    return admin
