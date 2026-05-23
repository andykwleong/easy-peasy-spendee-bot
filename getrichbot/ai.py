from __future__ import annotations

import base64
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from getrichbot.categories import ALL_CATEGORIES, SHOPPING_CATEGORIES, category_guidance_text
from getrichbot.models import ExpenseRecord


class EntryUpdate(BaseModel):
    entry_id: str
    amount: float | None = None
    category: str | None = None
    date: str | None = Field(default=None, description="YYYY-MM-DD if changing the expense date")
    description: str | None = None


class ExpenseIntent(BaseModel):
    action: Literal["delete", "edit", "answer", "clarify", "ignore"]
    entry_ids: list[str] = Field(default_factory=list)
    updates: list[EntryUpdate] = Field(default_factory=list)
    answer: str | None = None
    clarification_question: str | None = None


class ExtractedExpense(BaseModel):
    amount: float | None = None
    category: str | None = None
    description: str | None = None
    date: str | None = Field(default=None, description="YYYY-MM-DD expense date if visible or inferable")
    confidence: float = Field(default=0, ge=0, le=1)


class ExpenseExtractionResult(BaseModel):
    found_expenses: bool
    expenses: list[ExtractedExpense] = Field(default_factory=list)
    clarification_question: str | None = None


class PendingInstruction(BaseModel):
    action: Literal["confirm_some", "update_some", "confirm_and_update", "clarify", "ignore"]
    confirm_positions: list[int] = Field(default_factory=list, description="1-based positions to confirm")
    update_positions: list[int] = Field(default_factory=list, description="1-based positions to update")
    category: str | None = None
    date: str | None = Field(default=None, description="YYYY-MM-DD date to apply")
    clarification_question: str | None = None


class AIInterpreter:
    def __init__(self, api_key: str, model: str):
        from openai import OpenAI

        self.client = OpenAI(api_key=api_key, timeout=45, max_retries=0)
        self.model = model

    def interpret(self, message: str, records: list[ExpenseRecord], today: date, logged_by: str) -> ExpenseIntent:
        record_text = "\n".join(record.compact() for record in records[-80:]) or "No expense rows available."
        category_text = "\n".join(f"- {category}" for category in ALL_CATEGORIES)
        system = (
            "You help operate a household expense Google Sheet from Telegram messages. "
            "Return only a structured action. Use only entry IDs that appear in the provided rows. "
            "For delete/edit, choose the best matching existing row. If multiple rows match and the user did not specify enough detail, ask for clarification. "
            "For questions, answer from the provided rows only. If the answer is not in the rows, say you cannot find it. "
            "Do not invent expenses or entry IDs. Categories must match the allowed categories exactly. "
            + category_guidance_text()
        )
        user = (
            f"Today is {today.isoformat()} in Singapore. Telegram sender is {logged_by}.\n\n"
            f"Allowed categories:\n{category_text}\n\n"
            f"Recent expense rows:\n{record_text}\n\n"
            f"User message:\n{message}"
        )

        response = self.client.responses.parse(
            model=self.model,
            input=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            text_format=ExpenseIntent,
        )
        return response.output_parsed

    def extract_from_text(self, text: str, today: date, logged_by: str) -> ExpenseExtractionResult:
        category_text = "\n".join(f"- {category}" for category in ALL_CATEGORIES)
        shopping_text = _shopping_guidance()
        system = (
            "Extract household expenses from a short natural language message. "
            "Understand amounts written as words, including Singapore-style phrases like 'eighteen fifty dollars' meaning 18.50. "
            "Return one or more expenses. Use only allowed categories exactly. "
            f"{shopping_text} "
            "Resolve relative dates like yesterday using the provided current date. "
            "If a date appears once in a sentence with multiple expenses and later expenses do not have their own explicit date, "
            "apply that same date to all expenses in the sentence. "
            + category_guidance_text()
        )
        user = (
            f"Today is {today.isoformat()} in Singapore. Telegram sender is {logged_by}.\n\n"
            f"Allowed categories:\n{category_text}\n\n"
            f"Message:\n{text}"
        )
        response = self.client.responses.parse(
            model=self.model,
            input=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            text_format=ExpenseExtractionResult,
        )
        return response.output_parsed

    def interpret_pending_instruction(self, message: str, pending_lines: list[str], today: date) -> PendingInstruction:
        system = (
            "Interpret a user's instruction about pending extracted expenses. "
            "Positions are 1-based in the order shown. "
            "For phrases like 'first entry', use position 1; 'second one' position 2; 'last' means the final position. "
            "If the user says confirm all, set confirm_positions to all positions. "
            "If the user says change/update an entry, set update_positions and the new category and/or date. "
            "If the user combines instructions, return confirm_and_update. "
            "Use YYYY-MM-DD dates, resolving relative dates from today's date. "
            + category_guidance_text()
        )
        user = (
            f"Today is {today.isoformat()}.\n\n"
            "Pending expenses:\n"
            + "\n".join(pending_lines)
            + f"\n\nUser message:\n{message}"
        )
        response = self.client.responses.parse(
            model=self.model,
            input=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            text_format=PendingInstruction,
        )
        return response.output_parsed

    def extract_from_image(
        self,
        image_bytes: bytes,
        mime_type: str,
        today: date,
        logged_by: str,
    ) -> ExpenseExtractionResult:
        encoded = base64.b64encode(image_bytes).decode("ascii")
        category_text = "\n".join(f"- {category}" for category in ALL_CATEGORIES)
        shopping_text = _shopping_guidance()
        system = (
            "Extract all visible household expenses from a receipt, payment, or banking screenshot. "
            "Return structured data only. If there are multiple transactions, return all of them as separate expenses. "
            "If an amount or merchant is unclear, skip that row or ask a short clarification question. "
            "Use only the allowed categories exactly. "
            f"{shopping_text} If date is not visible, use today's date. "
            "If a weekday is shown without a full date, infer the most recent past matching weekday from today's date. "
            "For numeric dates like 12/5/26, interpret as DD/MM/YY for Singapore unless other context is obvious. "
            + category_guidance_text()
        )
        user = (
            f"Today is {today.isoformat()} in Singapore. Telegram sender is {logged_by}.\n\n"
            f"Allowed categories:\n{category_text}"
        )
        response = self.client.responses.parse(
            model=self.model,
            input=[
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": user},
                        {
                            "type": "input_image",
                            "image_url": f"data:{mime_type};base64,{encoded}",
                            "detail": "low",
                        },
                    ],
                },
            ],
            max_output_tokens=900,
            store=False,
            text_format=ExpenseExtractionResult,
        )
        return response.output_parsed

    def transcribe_audio(self, audio_bytes: bytes, filename: str = "voice.ogg") -> str:
        response = self.client.audio.transcriptions.create(
            model="gpt-4o-mini-transcribe",
            file=(filename, audio_bytes),
        )
        return response.text.strip()


def _shopping_guidance() -> str:
    me_category = SHOPPING_CATEGORIES.get("me")
    wife_category = SHOPPING_CATEGORIES.get("wife")
    if me_category and wife_category:
        return f"For shopping, use the sender's shopping category: {me_category} for Me, {wife_category} for My wife."
    if me_category:
        return f"For shopping from Me, use {me_category}."
    if wife_category:
        return f"For shopping from My wife, use {wife_category}."
    return "For shopping, use the most relevant allowed shopping category."
