/** Interface language. English strings are the keys; Hindi is looked up, anything missing stays English. */
export type Lang = "en" | "hi";

const read = (): Lang => { try { return (localStorage.getItem("vyum.lang") as Lang) || "en"; } catch { return "en"; } };
export let lang: Lang = read();
export function setLang(l: Lang) {
  try { localStorage.setItem("vyum.lang", l); } catch { /* private mode */ }
  location.reload();
}

const HI: Record<string, string> = {
  // rail and sidebar
  "Chats": "चैट", "Projects": "प्रोजेक्ट", "Office": "ऑफ़िस", "Mail": "मेल", "Overview": "सारांश", "Settings": "सेटिंग्स",
  "New chat": "नई चैट", "Recents": "हाल की चैट", "No projects yet": "अभी कोई प्रोजेक्ट नहीं", "Your chats appear here": "आपकी चैट यहाँ दिखेंगी",
  "Offline · models ready": "ऑफ़लाइन · मॉडल तैयार", "Web search on · files stay on this Mac": "वेब सर्च चालू · फ़ाइलें इसी Mac पर",
  "Hide sidebar": "साइडबार छिपाएँ", "Show sidebar": "साइडबार दिखाएँ", "Delete chat": "चैट मिटाएँ", "New project": "नया प्रोजेक्ट",
  // home and composer
  "Good morning": "सुप्रभात", "Good afternoon": "नमस्कार", "Good evening": "शुभ संध्या", "Working late": "देर तक काम",
  "How can I help?": "मैं कैसे मदद करूँ?",
  "Ask anything, attach files with +, or type / for commands": "कुछ भी पूछिए, + से फ़ाइलें जोड़िए, या कमांड के लिए / लिखिए",
  "Reply, or type / for commands": "जवाब लिखिए, या कमांड के लिए / लिखिए",
  "Ask about your documents (attach files with +), or type / for commands": "अपने दस्तावेज़ों के बारे में पूछिए (+ से फ़ाइलें जोड़िए)",
  "Open a folder as a project": "फ़ोल्डर को प्रोजेक्ट बनाएँ", "Compare documents": "दस्तावेज़ों की तुलना", "Summarize a file": "फ़ाइल का सार",
  "Find exact words": "सटीक शब्द खोजें", "Web": "वेब", "Think": "सोचें", "Chat": "चैट", "Documents": "दस्तावेज़", "Documents · Fast": "दस्तावेज़ · तेज़",
  "Compare": "तुलना", "Upload files": "फ़ाइलें अपलोड करें", "PDF, Word, PowerPoint, text, images": "PDF, Word, PowerPoint, टेक्स्ट, चित्र",
  "New project from a folder": "फ़ोल्डर से नया प्रोजेक्ट", "Every file in it is read and kept in sync": "उसकी हर फ़ाइल पढ़ी और अपडेट रखी जाती है",
  "Commands": "कमांड", "Web search": "वेब सर्च", "On · only search words leave this Mac": "चालू · सिर्फ़ खोज के शब्द बाहर जाते हैं",
  "Off · nothing leaves this Mac": "बंद · कुछ भी बाहर नहीं जाता",
  "General assistant. Uses its own knowledge, your attached files and, if Web is on, the web": "सामान्य सहायक। अपनी जानकारी, आपकी फ़ाइलें और (वेब चालू हो तो) वेब से जवाब",
  "Answers only from your files. Every sentence cited and verified, or it says not found": "सिर्फ़ आपकी फ़ाइलों से जवाब। हर वाक्य स्रोत के साथ जाँचा हुआ, वरना \"नहीं मिला\"",
  "Only from your files, one search, cited but not verified": "सिर्फ़ आपकी फ़ाइलों से, एक खोज, स्रोत के साथ पर बिना जाँच",
  "Side-by-side table of 2 to 5 files. Type what to compare": "2 से 5 फ़ाइलों की आमने-सामने तालिका। लिखिए किसकी तुलना करनी है",
  "What should be compared? e.g. payment terms and warranty (attach 2 to 5 files with +)": "किसकी तुलना करनी है? जैसे भुगतान की शर्तें और वारंटी (+ से 2–5 फ़ाइलें जोड़ें)",
  // answers
  "Not found in these documents": "इन दस्तावेज़ों में नहीं मिला", "Copy": "कॉपी", "Copied": "कॉपी हो गया", "Sources": "स्रोत",
  "Fast mode · not verified": "तेज़ मोड · जाँचा नहीं गया", "Verified": "जाँचा गया", "high confidence": "उच्च विश्वास",
  "medium confidence": "मध्यम विश्वास", "low confidence": "कम विश्वास", "Used your files · not verified": "आपकी फ़ाइलें इस्तेमाल हुईं · जाँचा नहीं गया",
  "Searched the web for": "वेब पर खोजा", "Nothing to search yet": "अभी खोजने के लिए कुछ नहीं है", "Thinking…": "सोच रहा है…",
  "Thought for": "सोचा", "Stop": "रोकें", "Stopped": "रोका गया", "Stopped · not verified": "रोका गया · जाँचा नहीं गया",
  "Stopped before the answer was written.": "जवाब लिखने से पहले रोक दिया गया।", "The file was not changed.": "फ़ाइल में कोई बदलाव नहीं हुआ।", "Calculated": "हिसाब लगाया", "Plan": "योजना", "try": "कोशिश", "Vyum is working on": "Vyum यहाँ काम कर रहा है:", "Dashboard": "डैशबोर्ड", "Sheet": "शीट", "Clear filters": "फ़िल्टर हटाएँ", "Back to sheet": "शीट पर वापस",
  "Computing the dashboard": "डैशबोर्ड बना रहा है", "of": "में से", "rows": "पंक्तियाँ", "from": "स्रोत",
  "computed by Vyum from your data": "Vyum ने आपके डेटा से गणना की", "New dashboard": "नया डैशबोर्ड", "Create": "बनाएँ", "Dashboard name": "डैशबोर्ड का नाम",
  "Delete dashboard": "डैशबोर्ड मिटाएँ", "Double-click to rename": "नाम बदलने के लिए दो बार क्लिक करें",
  "What should it show? e.g. revenue by region and month": "क्या दिखाना है? जैसे region और महीने के हिसाब से revenue",
  "Saved": "सेव हुआ", "step": "चरण", "steps": "चरण", "unsupported sentence removed": "बिना आधार का वाक्य हटाया", "unsupported sentences removed": "बिना आधार के वाक्य हटाए",
  // settings
  "Web search · SearXNG": "वेब सर्च · SearXNG",
  "Vyum searches the web through SearXNG, a search engine you run yourself. It is used only in Chat mode, only when Web is on, and only the search words are sent to it. Your files and conversations stay on this Mac.":
    "Vyum वेब पर SearXNG से खोजता है, जो आपका अपना सर्च इंजन है। यह सिर्फ़ चैट मोड में, वेब चालू होने पर इस्तेमाल होता है, और इसे सिर्फ़ खोज के शब्द भेजे जाते हैं। आपकी फ़ाइलें और बातचीत इसी Mac पर रहती हैं।", "Language": "भाषा", "Cancel": "रद्द करें", "Save": "सेव करें", "Test": "जाँचें",
  // office
  "What shall we make?": "क्या बनाएँ?", "Open local": "कंप्यूटर से खोलें", "Search file names and contents…": "फ़ाइल के नाम और अंदर लिखा खोजें…",
  "All": "सभी", "Docs": "डॉक्स", "Sheets": "शीट्स", "Slides": "स्लाइड्स", "Markdown": "मार्कडाउन", "HTML": "HTML", "PDF": "PDF",
  "Files you create or open appear here.": "आप जो फ़ाइलें बनाएँगे या खोलेंगे, वे यहाँ दिखेंगी।",
  "Saved to this Mac": "इस Mac पर सेव", "Saving…": "सेव हो रहा है…", "On this Mac": "इस Mac पर", "Could not save": "सेव नहीं हो सका",
  "Undo change": "बदलाव वापस लें", "Show in Finder": "Finder में दिखाएँ",
  "reads and edits this file": "यह फ़ाइल पढ़ता और बदलता है", "Insert chart": "चार्ट जोड़ें", "Freeze header": "हेडर रोकें",
  "Ask about this file, or tell me what to change. You will see the change in the file.": "इस फ़ाइल के बारे में पूछिए, या बताइए क्या बदलना है। बदलाव फ़ाइल में दिखेगा।",
  "Ask or tell Vyum what to change…": "पूछिए या बताइए क्या बदलना है…", "Describe what to write…": "बताइए क्या लिखना है…",
  "Summarize this document": "इस दस्तावेज़ का सार बताओ", "Fix spelling and grammar": "वर्तनी और व्याकरण ठीक करो",
  "Add an executive summary at the top": "ऊपर एक छोटा सारांश जोड़ो", "Make it more formal": "इसे ज़्यादा औपचारिक बनाओ",
  "What is the total?": "कुल कितना है?", "Add a totals row": "कुल की एक पंक्ति जोड़ो", "Add a column with the % change": "% बदलाव का कॉलम जोड़ो",
  "Sort by the largest amount": "सबसे बड़ी रकम से क्रम लगाओ", "Summarize this PDF": "इस PDF का सार बताओ", "What are the key points of this PDF?": "इस PDF के मुख्य बिंदु क्या हैं?", "Explain this": "इसे समझाओ", "Save the PDF": "PDF सेव करें", "Nothing was changed.": "कुछ नहीं बदला।", "Made {made} of {n} changes; the list shows which ones did not work.": "{n} में से {made} बदलाव हुए; सूची में देखें कौन-से नहीं हुए।", "The PDF was not saved.": "PDF सेव नहीं हुई।", "Not found in the PDF": "PDF में नहीं मिला", "Some steps could not be done; see the list.": "कुछ कदम नहीं हो पाए; सूची देखें।", "The editor did not answer.": "एडिटर ने जवाब नहीं दिया।", "Editing the PDF": "PDF में बदलाव कर रहा है", "Planning the edits": "बदलावों की योजना बना रहा है", "Finding the exact words in the PDF": "PDF में सही शब्द ढूँढ रहा है", "Highlight sources in the PDF": "स्रोतों को PDF में हाइलाइट करें", "{n} passages highlighted. Save the PDF to keep them.": "{n} हिस्से हाइलाइट हुए। रखने के लिए PDF सेव करें।", "What are the key dates?": "मुख्य तारीखें क्या हैं?",
  "Remove page 1": "पेज 1 हटाओ", "Convert to Word": "Word में बदलो", "Rotate": "घुमाएँ", "Extract page": "पेज अलग करें", "Delete page": "पेज मिटाएँ",
  // overview
  "Questions asked": "पूछे गए सवाल", "Office files": "ऑफ़िस फ़ाइलें", "Activity · last 14 days": "गतिविधि · पिछले 14 दिन",
  "Library by type": "प्रकार के अनुसार फ़ाइलें", "Privacy and status": "गोपनीयता और स्थिति", "Quick actions": "तुरंत करें",
  "Recent documents": "हाल के दस्तावेज़", "Recent chats": "हाल की चैट", "Create a document": "दस्तावेज़ बनाएँ", "Write an email": "ईमेल लिखें",
  // mail
  "Compose": "लिखें", "Inbox": "इनबॉक्स", "Starred": "तारांकित", "Sent": "भेजे गए", "Drafts": "ड्राफ़्ट", "Archive": "आर्काइव",
  "Spam": "स्पैम", "Trash": "ट्रैश", "All mail": "सभी मेल", "Ask Vyum": "Vyum से पूछें", "Reply": "जवाब दें", "Reply all": "सभी को जवाब",
  "Forward": "आगे भेजें", "Send": "भेजें", "Help me write": "लिखने में मदद", "Summarize this conversation": "इस बातचीत का सार",
  "Draft reply": "जवाब का ड्राफ़्ट", "Add your email": "अपना ईमेल जोड़ें", "Continue": "आगे बढ़ें", "Sign in": "साइन इन",
  "Use a different address": "दूसरा पता इस्तेमाल करें", "Password": "पासवर्ड", "Your name": "आपका नाम",
  "What needs my reply?": "किन मेल का जवाब देना है?", "Summarize today's emails": "आज के ईमेल का सार",
  "Any invoices or payments due?": "कोई इनवॉइस या भुगतान बाकी है?", "What did my manager ask for this week?": "इस हफ़्ते मेरे मैनेजर ने क्या माँगा?",
};

export const t = (s: string) => (lang === "hi" ? HI[s] ?? s : s);

/** The engine's live progress lines, translated by pattern. */
const STATUS: [RegExp, (m: RegExpMatchArray) => string][] = [
  [/^Searching (\d+) documents?$/, (m) => `${m[1]} दस्तावेज़ों में खोज रहा है`],
  [/^Reading (\d+) passages? from (\d+) documents?$/, (m) => `${m[2]} दस्तावेज़ों के ${m[1]} हिस्से पढ़ रहा है`],
  [/^Checking (\d+) claims? against the sources$/, (m) => `${m[1]} बातें स्रोतों से जाँच रहा है`],
  [/^Writing the answer$/, () => "जवाब लिख रहा है"], [/^Thinking$/, () => "सोच रहा है"], [/^Starting$/, () => "शुरू हो रहा है"],
  [/^Reading the conversation$/, () => "बातचीत पढ़ रहा है"], [/^Looking through your files$/, () => "आपकी फ़ाइलें देख रहा है"],
  [/^Deciding whether to search the web$/, () => "तय कर रहा है कि वेब पर खोजना है या नहीं"],
  [/^Searching the web for (.*)$/, (m) => `वेब पर खोज रहा है: ${m[1]}`], [/^Writing$/, () => "लिख रहा है"],
  [/^Reading (\d+) sources?$/, (m) => `${m[1]} स्रोत पढ़ रहा है`], [/^Working it out$/, () => "हिसाब लगा रहा है"],
  [/^Reading (.+)$/, (m) => `${m[1]} पढ़ रहा है`], [/^Editing the document$/, () => "दस्तावेज़ बदल रहा है"],
  [/^Saving$/, () => "सेव कर रहा है"], [/^Saving the changed parts$/, () => "बदले हुए हिस्से सेव कर रहा है"],
  [/^Applying (\d+) changes?$/, (m) => `${m[1]} बदलाव लागू कर रहा है`], [/^Building (\d+) slides$/, (m) => `${m[1]} स्लाइड बना रहा है`],
  [/^Rebuilding (\d+) slides$/, (m) => `${m[1]} स्लाइड फिर से बना रहा है`], [/^Planning the changes$/, () => "बदलाव तय कर रहा है"],
  [/^Searching your mail$/, () => "आपके मेल में खोज रहा है"], [/^Summarizing$/, () => "सार बना रहा है"],
  [/^Understanding the question$/, () => "सवाल समझ रहा है"], [/^Stopping$/, () => "रोक रहा है"], [/^Calculating$/, () => "हिसाब लगा रहा है"],
  [/^Planning the formulas$/, () => "फ़ॉर्मूले तय कर रहा है"], [/^Fixing the formulas$/, () => "फ़ॉर्मूले ठीक कर रहा है"],
  [/^Writing (\d+) steps? as formulas$/, () => "फ़ॉर्मूले लिख रहा है"],
  [/^Translating into .+ and checking the translation$/, () => "अनुवाद कर रहा है और उसकी जाँच कर रहा है"],
];
export const tStatus = (s: string) => {
  if (lang !== "hi") return s;
  for (const [re, f] of STATUS) { const m = s.match(re); if (m) return f(m); }
  return s;
};

/** Numbers as Indian readers expect when the app is in Hindi (12,03,75,718). */
export const num = (n: number) => n.toLocaleString(lang === "hi" ? "en-IN" : undefined);
