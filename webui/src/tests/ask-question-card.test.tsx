import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { AskQuestionCard } from "@/components/thread/AskQuestionCard";
import type { AskQuestionRequest } from "@/lib/types";

const QUESTION: AskQuestionRequest = {
  question_id: "q-1",
  question: "接下来你想把精力放在哪块？",
  options: [
    { label: "继续阶段2", description: "按 spec 顺序推进", recommended: true },
    { label: "回头验证阶段1" },
  ],
};

describe("AskQuestionCard", () => {
  it("renders numbered options with the recommended badge", () => {
    render(<AskQuestionCard question={QUESTION} onAnswer={vi.fn()} />);
    expect(screen.getByTestId("ask-question-card")).toHaveTextContent(QUESTION.question);
    expect(screen.getByTestId("ask-question-option-1")).toHaveTextContent("继续阶段2");
    expect(screen.getByTestId("ask-question-option-1")).toHaveTextContent("按 spec 顺序推进");
    expect(screen.getByTestId("ask-question-option-2")).toHaveTextContent("回头验证阶段1");
  });

  it("submits the option label on click", () => {
    const onAnswer = vi.fn();
    render(<AskQuestionCard question={QUESTION} onAnswer={onAnswer} />);
    fireEvent.click(screen.getByTestId("ask-question-option-2"));
    expect(onAnswer).toHaveBeenCalledWith("q-1", "回头验证阶段1");
  });

  it("submits a custom answer from the input", () => {
    const onAnswer = vi.fn();
    render(<AskQuestionCard question={QUESTION} onAnswer={onAnswer} />);
    fireEvent.change(screen.getByTestId("ask-question-custom-input"), {
      target: { value: "别的活儿" },
    });
    fireEvent.submit(screen.getByTestId("ask-question-form"));
    expect(onAnswer).toHaveBeenCalledWith("q-1", "别的活儿");
  });

  it("selects an option with the number key", () => {
    const onAnswer = vi.fn();
    render(<AskQuestionCard question={QUESTION} onAnswer={onAnswer} />);
    fireEvent.keyDown(window, { key: "1" });
    expect(onAnswer).toHaveBeenCalledWith("q-1", "继续阶段2");
  });
});
