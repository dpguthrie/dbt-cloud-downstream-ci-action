# stdlib
import asyncio
import os

# third party
import pytest
from dotenv import load_dotenv

# first party
from src.main import main

load_dotenv()


def test_successful_exit():
    os.environ["GIT_SHA"] = os.getenv("GIT_SHA_SUCCESS")
    with pytest.raises(SystemExit) as e:
        asyncio.run(main())

    assert e.type == SystemExit
    assert e.value.code == 0
