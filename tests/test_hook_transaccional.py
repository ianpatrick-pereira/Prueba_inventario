"""El hook de tests/conftest.py rechaza pruebas transaccionales sin serialized_rollback=True."""
import pytest

pytest_plugins = ["pytester"]


@pytest.fixture
def proyecto_con_hook(pytester):
    pytester.makeconftest("from tests.conftest import pytest_runtest_setup  # noqa: F401\n")
    return pytester


def test_transaccional_sin_serialized_rollback_falla_con_mensaje_claro(proyecto_con_hook):
    proyecto_con_hook.makepyfile(
        """
        import pytest

        @pytest.mark.django_db(transaction=True)
        def test_x():
            pass
        """
    )
    r = proyecto_con_hook.runpytest_inprocess("-p", "no:django")
    r.assert_outcomes(errors=1)
    r.stdout.fnmatch_lines(["*serialized_rollback=True*"])


def test_transaccional_con_serialized_rollback_pasa_el_hook(proyecto_con_hook):
    proyecto_con_hook.makepyfile(
        """
        import pytest

        @pytest.mark.django_db(transaction=True, serialized_rollback=True)
        def test_x():
            pass

        @pytest.mark.django_db
        def test_y():
            pass

        def test_z():
            pass
        """
    )
    r = proyecto_con_hook.runpytest_inprocess("-p", "no:django")
    r.assert_outcomes(passed=3)
