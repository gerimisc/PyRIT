# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from abc import ABC
import json
import logging
import os
from openai import RateLimitError
from tenacity import after_log, retry, retry_if_exception_type, stop_after_attempt, wait_random_exponential
from typing import Callable

from pyrit.exceptions.exceptions_helpers import extract_json_from_string, remove_end_md_json, remove_start_md_json
from pyrit.models import construct_response_from_request, PromptRequestPiece, PromptRequestResponse

RETRY_MAX_NUM_ATTEMPTS = int(os.getenv("RETRY_MAX_NUM_ATTEMPTS", 5))
RETRY_WAIT_MIN_SECONDS = int(os.getenv("RETRY_WAIT_MIN_SECONDS", 1))
RETRY_WAIT_MAX_SECONDS = int(os.getenv("RETRY_WAIT_MAX_SECONDS", 60))

logger = logging.getLogger(__name__)


class PyritException(Exception, ABC):

    def __init__(self, status_code=500, *, message: str = "An error occurred"):
        self.status_code = status_code
        self.message = message
        super().__init__(f"Status Code: {status_code}, Message: {message}")

    def process_exception(self) -> str:
        """
        Logs and returns a string representation of the exception.
        """
        log_message = f"{self.__class__.__name__} encountered: Status Code: {self.status_code}, Message: {self.message}"
        logger.error(log_message)
        # Return a string representation of the exception so users can extract and parse
        return json.dumps({"status_code": self.status_code, "message": self.message})


class BadRequestException(PyritException):
    """Exception class for bad client requests."""

    def __init__(self, status_code: int = 400, *, message: str = "Bad Request"):
        super().__init__(status_code, message=message)


class RateLimitException(PyritException):
    """Exception class for authentication errors."""

    def __init__(self, status_code: int = 429, *, message: str = "Rate Limit Exception"):
        super().__init__(status_code, message=message)


class EmptyResponseException(BadRequestException):
    """Exception class for empty response errors."""

    def __init__(self, status_code: int = 204, *, message: str = "No Content"):
        super().__init__(status_code=status_code, message=message)


class InvalidJsonException(PyritException):
    """Exception class for blocked content errors."""

    def __init__(self, *, message: str = "Invalid JSON Response"):
        super().__init__(message=message)


class MissingPromptPlaceholderException(PyritException):
    """Exception class for missing prompt placeholder errors."""

    def __init__(self, *, message: str = "No prompt placeholder"):
        super().__init__(message=message)


def handle_bad_request_exception(
    response_text: str,
    request: PromptRequestPiece,
    is_content_filter=False,
) -> PromptRequestResponse:

    if (
        "content_filter" in response_text
        or "Invalid prompt: your prompt was flagged as potentially violating our usage policy." in response_text
        or is_content_filter
    ):
        # Handle bad request error when content filter system detects harmful content
        bad_request_exception = BadRequestException(400, message=response_text)
        resp_text = bad_request_exception.process_exception()
        response_entry = construct_response_from_request(
            request=request, response_text_pieces=[resp_text], response_type="error", error="blocked"
        )
    else:
        raise

    return response_entry


def pyrit_target_retry(func: Callable) -> Callable:
    """
    A decorator to apply retry logic with exponential backoff to a function.

    Retries the function if it raises RateLimitError or EmptyResponseException,
    with a wait time between retries that follows an exponential backoff strategy.
    Logs retry attempts at the INFO level and stops after a maximum number of attempts.

    Args:
        func (Callable): The function to be decorated.

    Returns:
        Callable: The decorated function with retry logic applied.
    """
    global RETRY_MAX_NUM_ATTEMPTS, RETRY_WAIT_MIN_SECONDS, RETRY_WAIT_MAX_SECONDS

    return retry(
        reraise=True,
        retry=retry_if_exception_type(RateLimitError) | retry_if_exception_type(EmptyResponseException),
        wait=wait_random_exponential(min=RETRY_WAIT_MIN_SECONDS, max=RETRY_WAIT_MAX_SECONDS),
        after=after_log(logger, logging.INFO),
        stop=stop_after_attempt(RETRY_MAX_NUM_ATTEMPTS),
    )(func)


def pyrit_json_retry(func: Callable) -> Callable:
    """
    A decorator to apply retry logic with exponential backoff to a function.

    Retries the function if it raises a JSON error,
    with a wait time between retries that follows an exponential backoff strategy.
    Logs retry attempts at the INFO level and stops after a maximum number of attempts.

    Args:
        func (Callable): The function to be decorated.

    Returns:
        Callable: The decorated function with retry logic applied.
    """
    global RETRY_MAX_NUM_ATTEMPTS, RETRY_WAIT_MIN_SECONDS, RETRY_WAIT_MAX_SECONDS

    return retry(
        reraise=True,
        retry=retry_if_exception_type(InvalidJsonException),
        wait=wait_random_exponential(min=RETRY_WAIT_MIN_SECONDS, max=RETRY_WAIT_MAX_SECONDS),
        after=after_log(logger, logging.INFO),
        stop=stop_after_attempt(RETRY_MAX_NUM_ATTEMPTS),
    )(func)


def remove_markdown_json(response_msg: str) -> str:
    """
    Checks if the response message is in JSON format and removes Markdown formatting if present.

    Args:
        response_msg (str): The response message to check.

    Returns:
        str: The response message without Markdown formatting if present.
    """

    response_msg = remove_start_md_json(response_msg)
    response_msg = remove_end_md_json(response_msg)

    # Validate if the remaining response message is valid JSON. If it's still not valid
    # after removing the markdown notation, try to extract JSON from within the string.
    try:
        json.loads(response_msg)
        return response_msg
    except json.JSONDecodeError:
        response_msg = extract_json_from_string(response_msg)
        try:
            json.loads(response_msg)
            return response_msg
        except json.JSONDecodeError:
            return "Invalid JSON response: {}".format(response_msg)


def pyrit_placeholder_retry(func: Callable) -> Callable:
    """
    A decorator to apply retry logic.

    Retries the function if it raises MissingPromptPlaceholderException.
    Logs retry attempts at the INFO level and stops after a maximum number of attempts.

    Args:
        func (Callable): The function to be decorated.

    Returns:
        Callable: The decorated function with retry logic applied.
    """

    global RETRY_MAX_NUM_ATTEMPTS

    return retry(
        reraise=True,
        retry=retry_if_exception_type(MissingPromptPlaceholderException),
        after=after_log(logger, logging.INFO),
        stop=stop_after_attempt(RETRY_MAX_NUM_ATTEMPTS),
    )(func)
