/**
 * English resources, and the shape every other language must satisfy.
 *
 * `Translation` is derived from this object rather than declared separately,
 * so adding a key here is immediately a compile error in ru.ts and ar.ts until
 * it is translated. That is the whole reason this is a plain typed object and
 * not a runtime key lookup: a missing translation fails the build, not the
 * page.
 *
 * Values that vary (counts, names, references) are functions rather than
 * strings with placeholders, so the argument types are checked too and each
 * language can put the substitution wherever its grammar needs it.
 */
export const en = {
  code: "en",
  name: "English",

  common: {
    loading: "Loading…",
    search: "Search",
    clear: "Clear",
    cancel: "Cancel",
    reset: "Reset",
    previous: "← Previous",
    next: "Next →",
    notProvided: "(not provided)",
    sending: "Sending…",
  },

  nav: {
    title: "Complaint Intake System",
    tagline: "Report a problem by email, no form and no account",
    taglineStaff: "Internal support dashboard",
    home: "Home",
    demo: "Try the demo",
    supportInbox: "Support inbox",
    github: "GitHub ↗",
    backToSite: "← Back to site",
    staff: "Staff",
    language: "Language",
  },

  footer: {
    reportPrefix: "To report a problem, email",
    builtWith: "Portfolio project · FastAPI · React · Docker ·",
    source: "source",
  },

  home: {
    heroTitle: "Have a problem?",
    heroLede: "Send us an email describing your problem.",
    heroCta: "Send a complaint",
    heroSubject: "Complaint",
    noForm: "No form. No account. Just email us.",
    explainer:
      "Describe your problem in your own words. We'll reply by email and ask for any information we still need, one question at a time.",
    steps: [
      {
        title: "Send your complaint",
        body: "Email us and describe what happened.",
      },
      {
        title: "Answer a few questions",
        body: "We'll ask only for the information needed to process your complaint.",
      },
      {
        title: "Get a ticket",
        body: "Once everything is complete, your case is turned into a structured ticket for our support team.",
      },
    ],
    demoInviteTitle: "Want to see it work first?",
    demoInviteBody:
      "Watch an example conversation play out in your browser. It's a preview; to send a real complaint, email the address above.",
    demoInviteCta: "Try the demo",

    about: {
      summary: "About this project",
      hint: "engineering notes for developers & recruiters",
      intro:
        "An email-only complaint intake system: unstructured customer emails become structured support tickets through a deterministic workflow engine with AI-assisted extraction. Built as a portfolio project, so the engineering notes below are for developers and recruiters.",
      sourceLink: "Source on GitHub ↗",

      howItWorks: "How it works",
      flow: [
        { lead: "Customer emails", rest: " the complaints mailbox, free-form, in any of three languages." },
        { lead: "The poller", rest: " fetches new mail over IMAP and hands it to the intake service." },
        { lead: "Classification", rest: " decides the complaint type; low confidence asks the customer to clarify instead of guessing." },
        { lead: "Extraction", rest: " pulls whatever details the message already contains, in any order." },
        { lead: "The engine", rest: " compares that against the required fields and asks for the single next one that is missing or invalid." },
        { lead: "The customer replies", rest: " and the thread continues: steps 4–5 repeat until nothing is outstanding." },
        { lead: "A ticket is created", rest: " with a numeric reference, the customer gets a confirmation, and support receives the structured summary." },
        { lead: "Support works the ticket", rest: " in an internal dashboard (the collected fields, the conversation that produced them, and the status lifecycle), behind the staff API key." },
      ],

      boundaryTitle: "AI vs deterministic logic",
      boundaryNote: "The boundary is the core design rule, and it is enforced by tests.",
      aiAssists: "AI assists with",
      aiList: [
        "Classifying the complaint type",
        "Extracting field values from prose",
        "Summarising the problem",
        "Detecting the language",
        "Wording the customer-facing reply",
      ],
      codeOwns: "Deterministic code owns",
      codeList: [
        "Which fields are required",
        "Whether a value is valid",
        "Whether the complaint is complete",
        "When a ticket is created",
        "The ticket reference itself",
      ],
      boundaryFooter:
        "An email instructing the system to “mark this complete” changes nothing: completeness is a schema check, not a suggestion.",

      typesTitle: "Complaint types",
      typesNote:
        "Loaded live from the backend’s YAML schema registry, the same source the engine uses, so business rules live in configuration, not in code or prompts.",
      openSchema:
        "Open-ended: no fixed field set; requires a sufficiently detailed description.",
      requiredFields: (n: number) => `${n} required fields, collected one at a time.`,

      reliabilityTitle: "Reliability & security",
      reliability: [
        {
          title: "Idempotency",
          body: "Each email’s Message-ID is stored under a unique index, so a redelivered message cannot produce a duplicate conversation, question, or ticket.",
        },
        {
          title: "Thread continuity",
          body: "Replies are matched by In-Reply-To and the References chain, with an opaque subject token as fallback, never by sender address alone, since one customer may have several complaints open.",
        },
        {
          title: "Crash safety",
          body: "A message is acknowledged to the mail server only after its transaction commits. A failure mid-turn rolls back and retries cleanly instead of losing the complaint or half-answering it.",
        },
        {
          title: "Loop protection",
          body: "Auto-replies, bounces, and the system’s own outgoing mail are ignored rather than answered, so it can never get into a reply loop with itself or another responder.",
        },
        {
          title: "Hostile input",
          body: "Header-derived values are sanitised and bounded at the model boundary, so a crafted subject cannot wedge the poller or exceed a database column.",
        },
        {
          title: "Data separation",
          body: "Real complaints require an API key. The demo reads only synthetic conversations, filtered in the database query instead of hidden by the UI, so what you type there is sandboxed, and real customer data is unreachable from it.",
        },
        {
          title: "Ticket lifecycle",
          body: "Closing a ticket ends it. A later email from the same customer starts a new ticket with its own history, even if it replies to the old thread: the lifecycle outranks the email headers.",
        },
        {
          title: "Localisation",
          body: "Replies follow the language of the customer’s latest message (English, Arabic, and Russian), falling back to the thread’s known language when a message carries no clear signal.",
        },
      ],

      architectureTitle: "Architecture",
      stackTitle: "Technology stack",
      stack: [
        { lead: "Backend", rest: ": Python 3.12, FastAPI, SQLAlchemy 2.0, Alembic, Pydantic v2" },
        { lead: "Email", rest: ": IMAP/SMTP via the standard library, behind a swappable provider interface" },
        { lead: "AI layer", rest: ": pluggable, a deterministic rule-based provider by default, local Ollama optional" },
        { lead: "Frontend", rest: ": React 18, TypeScript, Vite" },
        { lead: "Infrastructure", rest: ": Docker Compose, Nginx reverse proxy, SQLite on a persistent volume, UFW-firewalled VPS" },
        { lead: "Tests", rest: ": automated coverage of the conversation lifecycle, threading, idempotency, retry behaviour, authentication, the ML layer, and security boundaries" },
      ],
      deploymentStatus: "Deployment status:",
      providerLine: (ai: string, email: string) => `AI provider ${ai}, email provider ${email}.`,
      mockNote: (address: string) =>
        ` This instance runs on the mock provider, so the demo stands in for the customer’s mail client and no real mail is sent or received. The deployed instance at startplus.tech runs the same code against the ${address} mailbox over IMAP and SMTP.`,
    },
  },

  demo: {
    bannerLead: "This is a demo.",
    bannerBody:
      "It simulates the email conversation in your browser using the same engine that handles real mail. To send a real complaint, email",
    scenariosTitle: "Demo scenarios",
    scenariosNote:
      "Each one sends real emails through the real engine. Nothing is scripted on the system's side.",
    scenarios: {
      vague: {
        name: "Vague complaint",
        teaches:
          "The first email is too vague to classify, so the engine asks the customer to explain rather than guessing a complaint type.",
      },
      complete: {
        name: "Complete in one email",
        teaches:
          "Everything needed arrives in the first message, so the engine skips straight to creating the ticket instead of asking questions it already has answers to.",
      },
      invalid: {
        name: "Invalid value, then corrected",
        teaches:
          "A value fails validation, so the engine asks again for that one field rather than accepting it or starting over.",
      },
      multiturn: {
        name: "Multi-turn with bare answers",
        teaches:
          "The customer answers with bare values and no context, and the engine still attributes each one to the field it asked for.",
      },
      freeform: {
        name: "Write your own",
        teaches: "Send whatever you like and watch the engine work it out.",
      },
    },
    threadWith: (address: string) => `Thread with ${address}`,
    noMessages: "No messages yet, send the first email",
    mailboxEmpty:
      "This is a mailbox, not a form. Send the first email to start the conversation.",
    delivering: "Delivering email…",
    unreachable: (message: string) => `Could not reach the intake service: ${message}`,
    sendFailed: "Something went wrong sending that email.",
    writeAsCustomer: "Write an email as the customer",
    writePlaceholder: "e.g. My withdrawal has not arrived and I am worried.",
    sendEmail: "Send email",
    scenarioComplete: "Scenario complete",
    scenarioCompleteTicket: (reference: string) =>
      `: ticket ${reference} was created. Open the Support inbox tab to see what support received.`,
    runAgain: "Run it again",
    nextEmailFrom: (who: string) => `Next email from ${who}`,
    sendFirst: "Send first email",
    sendReply: "Send this reply",

    understoodTitle: "What the engine understood",
    understoodEmpty: "Structured data appears here as the engine extracts it from the emails.",
    complaintType: "Complaint type",
    notClassified: "not yet classified",
    collectedSoFar: "Collected so far",
    nothingExtracted: "Nothing extracted yet.",
    needsCorrecting: (fields: string) => `Needs correcting: ${fields}`,
    stillNeeded: "Still needed",
    stillNeededNote: (fields: string) => `${fields}, asked for one at a time, in order.`,
    ticketCreated: "Ticket",
    ticketCreatedSuffix: "created",

    from: "From",
    to: "To",
    subject: "Subject",
    customer: "Customer",
    supportSystem: "Support system",
  },

  inbox: {
    title: "Support inbox",
    subtitle: "Demo view: structured tickets created from the email conversations above",
    empty: "No tickets yet. Run a scenario in the Customer mailbox tab and one will appear here.",
    backToInbox: "← Back to inbox",
    loadFailed: (message: string) => `Could not load tickets: ${message}`,
    ticketGone: (reference: string) => `Ticket ${reference} is no longer available.`,
    detailFailed: "Could not load that ticket.",
    demoLimit:
      "Replying and changing status are staff actions that need an API key, so they are not available in this public demo. The support dashboard uses this same screen with those controls enabled.",
  },

  dashboard: {
    title: "Support tickets",
    subtitle: "Internal: real tickets created by email intake",
    signOut: "Sign out",
    empty: "No tickets yet. They appear here once an email conversation is complete.",
    notSignedIn: "Not signed in.",

    signInTitle: "Staff access",
    notConfigured: "The staff API is not configured on this server.",
    noKeySet: "No staff key is set.",
    notConfiguredBody:
      "The server refuses staff requests entirely rather than serving customer data without authentication, so there is nothing to sign in to until an operator sets STAFF_API_KEY in the deployment configuration.",
    signInBody:
      "This dashboard shows real customer complaints. Enter the staff API key to continue. It is held for this browser tab only and is never stored in the application.",
    keyRejected: "That key was not accepted.",
    keyLabel: "Staff API key",
    keyPlaceholder: "Paste the key",
    signIn: "Sign in",

    replyOpen: "Reply to customer",
    replyTitle: "Reply to customer",
    replyTo: "To",
    replyFromTicket: "· from this ticket",
    replySubject: "Subject",
    replyMessage: "Message",
    replyPlaceholder: "Write your reply to the customer…",
    replySend: "Send email",
    replySent: "Email sent.",
    replyNotDelivered: "Not delivered.",
  },

  tickets: {
    all: "All",
    searchPlaceholder: "Reference or customer email…",
    searchLabel: "Search tickets",
    filterByStatus: "Filter by status",
    noMatches: "No tickets match those filters.",
    noTicketsInGroup: "No tickets",
    page: (page: number, count: number) => `Page ${page} of ${count}`,

    backToTickets: "← Back to tickets",
    ticketNumber: (reference: string) => `Ticket #${reference}`,
    status: "Status",
    openedAt: "opened",
    lastActivity: "last activity",
    noStructured: "No structured information was collected.",
    conversationHistory: "Conversation history",
    messageCount: (n: number) => `· ${n} messages`,
    noMessages: "No messages recorded.",
    customer: "Customer",
    system: "System",
    from: "From",
    subject: "Subject",
    evidenceVerbatim: "Found word for word in the customer's email",
    evidenceSource: "From the email:",
  },

  ml: {
    similarTitle: "Similar tickets",
    similarNote: "Suggestions from the similarity model. Tickets are never merged automatically.",
    similarNone: "No similar tickets.",
    similarError: "Could not load suggestions.",
    possibleDuplicate: "Possible duplicate",
    similar: "Similar",
    match: (pct: number) => `${pct}% match`,
    reasons: {
      semantic_similarity: "similar wording",
      same_customer: "same customer",
      same_type: "same complaint type",
    } as Record<string, string>,
    sameField: (label: string) => `same ${label.toLowerCase()}`,
    incidentsTitle: "Possible incidents",
    incidentsNote:
      "Unusual bursts of similar complaints, found by clustering and a burst test against each topic's own history. For investigation only - nothing is changed automatically.",
    incidentsNone: (n: number, hours: number) =>
      `No unusual activity in the last ${hours} hours (${n} complaints analysed).`,
    incidentsError: "Incident detection is unavailable right now.",
    incidentSummary: (n: number, expected: number, hours: number) =>
      `${n} similar complaints in ${hours} h (usually ${expected.toFixed(1)})`,
    severity: { high: "High", medium: "Medium" } as Record<string, string>,
    keywords: "Key words",
    openComplaints: (n: number) => `${n} still collecting details`,
    volumeSpikeLabel: "Volume spike",
    volumeSpike: (label: string, n: number, expected: number) =>
      `${label}: ${n} in the window (usually ${expected.toFixed(1)})`,
    demoTag: "demo data",
    health: {
      title: "Model health",
      note: (n: number, days: number) =>
        `${n} classification decisions in the last ${days} days. Live behaviour; the offline evaluation is in docs/ml/evaluation.md.`,
      classifier: "Classifier",
      embedder: "Embeddings",
      decisions: "Decided by",
      abstention: "Model abstained",
      agreement: "Agrees with the rules",
      latency: "Classifier latency (p95)",
      rejected: "Values refused (no evidence)",
      drift: "Traffic drift",
      corrections: "Staff corrections",
      worker: "Background worker",
      embedderFallback: "The ONNX embedding model is configured but not loaded; using the fallback.",
      driftStatus: {
        stable: "stable",
        moderate_shift: "moderate shift",
        significant_shift: "significant shift",
        insufficient_data: "not enough data yet",
      } as Record<string, string>,
    },
    correctTitle: "Correct this ticket",
    correctionCount: (n: number) => `${n} correction${n === 1 ? "" : "s"}`,
    correctNote:
      "A correction updates the ticket and is kept, with what the system originally had, as training feedback. No model is retrained automatically.",
    correctType: "Complaint type",
    correctField: "Field",
    correctValue: "Correct value",
    correctNoteLabel: "Note (optional)",
    saveCorrection: "Save correction",
    correctionSaved: "Correction saved.",
    originalFrom: (source: string) => `was from ${source}`,
    sources: {
      rules: "the keyword rules",
      ml: "the classifier",
      provider: "the AI provider",
      customer_message: "the customer's email",
      employee: "a staff correction",
      missing: "nothing (not collected)",
      unknown: "an unknown source",
    } as Record<string, string>,
  },

  groups: {
    deposit: "Deposits",
    withdrawal: "Withdrawals",
    other: "Other",
  },

  /**
   * Singular complaint-type names. The backend registry supplies these too,
   * but only in English, so they are translated here and fall back to the
   * registry label for any type not listed.
   */
  complaintTypes: {
    deposit: "Deposit problem",
    withdrawal: "Withdrawal problem",
    other: "Other issue",
  } as Record<string, string>,

  status: {
    open: "Open",
    collecting_info: "Collecting information",
    validating: "Validating",
    completed: "Completed",
    abandoned: "Abandoned",
    new: "New",
    in_progress: "In progress",
    resolved: "Resolved",
    closed: "Closed",
  },

  /** Fallbacks only: real labels come from the backend schema registry. */
  fields: {
    user_id: "User ID",
    account_email: "Account email",
    withdrawal_transaction_id: "Withdrawal transaction ID",
    source_wallet_or_account: "Source wallet/account",
    transaction_date: "Transaction date",
    deposit_method: "Deposit method",
    problem_description: "Problem description",
  },
};

export type Translation = typeof en;
