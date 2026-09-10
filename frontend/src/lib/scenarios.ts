/**
 * Guided demo scenarios.
 *
 * Each scenario is only a list of customer emails. Every reply shown in the
 * demo comes from the real backend processing those messages - nothing here
 * scripts the system's side, and the outcomes below were verified against the
 * running engine rather than assumed.
 */
export interface Scenario {
  id: string;
  name: string;
  /** One line explaining what a viewer should watch for. */
  teaches: string;
  from: string;
  customerName: string;
  subject: string;
  messages: string[];
}

export const SCENARIOS: Scenario[] = [
  {
    id: "vague",
    name: "Vague complaint",
    teaches:
      "The first email is too vague to classify, so the engine asks the customer to explain rather than guessing a complaint type.",
    from: "amelia.hart@example.com",
    customerName: "Amelia Hart",
    subject: "Problem with my account",
    messages: [
      "Hello, I have a problem with my account and I am not happy.",
      "It is about a withdrawal I requested that never arrived.",
      "U-482913",
      "amelia.hart@example.com",
      "TXN-4c81de92",
    ],
  },
  {
    id: "complete",
    name: "Complete in one email",
    teaches:
      "Everything needed arrives in the first message, so the engine skips straight to creating the ticket instead of asking questions it already has answers to.",
    from: "noah.reed@example.com",
    customerName: "Noah Reed",
    subject: "Withdrawal has not arrived",
    messages: [
      "My withdrawal has not arrived. My user id is U-771204, my account email is " +
        "noah.reed@example.com and the transaction id is TXN-9f3a12bc.",
    ],
  },
  {
    id: "invalid",
    name: "Invalid value, then corrected",
    teaches:
      "A malformed email address is detected by schema validation. The engine explains the problem, asks for the same field again, and only moves on once it is valid.",
    from: "sofia.marino@example.com",
    customerName: "Sofia Marino",
    subject: "Cannot withdraw my funds",
    messages: [
      "I cannot withdraw my money, it has been stuck for two days.",
      "U-660145",
      "Account email: sofia.marino@example (I think that's right)",
      "Sorry, it is sofia.marino@example.com",
      "TXN-77aa0011",
    ],
  },
  {
    id: "multiturn",
    name: "Multi-turn with bare answers",
    teaches:
      "The customer replies with bare values and no labels. Each is understood as the answer to the one field that was just asked - contextual extraction, one field at a time.",
    from: "liam.okafor@example.com",
    customerName: "Liam Okafor",
    subject: "Deposit missing from my balance",
    messages: [
      "My deposit never showed up in my balance.",
      "U-330871",
      "liam.okafor@example.com",
      "Sberbank wallet",
      "2026-09-08",
      "I paid by bank transfer and the money left my account but never arrived.",
    ],
  },
];

export const FREEFORM: Scenario = {
  id: "freeform",
  name: "Write your own",
  teaches: "Send whatever you like and watch the engine work it out.",
  from: "you@example.com",
  customerName: "",
  subject: "Problem with my account",
  messages: [],
};
