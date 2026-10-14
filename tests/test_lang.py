"""Indian languages: detection, digits, Indian number grouping, sentence ends and localized fixed phrases."""
from engine import lang
from engine.harness import identifiers, split_sentences


def test_detects_scripts_hinglish_and_english():
    cases = {"Acme समझौते में भुगतान की शर्तें क्या हैं?": "hi", "Globex agreement me warranty kitne mahine ki hai?": "hinglish",
             "payment terms batao": "hinglish", "कराराचा कालावधी किती आहे?": "mr", "ஒப்பந்தத்தின் காலம் என்ன?": "ta",
             "চুক্তির মেয়াদ কত?": "bn", "ఒప్పందం గడువు ఎంత?": "te", "معاہدے کی مدت کیا ہے؟": "ur",
             "What is the warranty?": "en", "Is this ok?": "en", "Tell me the payment terms": "en"}
    assert {t: lang.detect(t) for t in cases} == cases


def test_digits_and_indian_grouping():
    assert lang.ascii_digits("राशि १२,४०० है") == "राशि 12,400 है"
    assert lang.indian_number(120375718) == "12,03,75,718" and lang.indian_number(12400) == "12,400"
    assert lang.indian_number(-1234567.5, 2) == "-12,34,567.50"
    assert identifiers("अनुबंध संख्या ०५०१२ की भुगतान शर्त क्या है?") == ["05012"]


def test_danda_ends_sentences_and_gaps_are_recognised():
    assert split_sentences("कर्मचारियों को 24 दिन की छुट्टी मिलती है [1]। देरी पर 1.5% ब्याज लगता है [2]।") == [
        "कर्मचारियों को 24 दिन की छुट्टी मिलती है [1]।", "देरी पर 1.5% ब्याज लगता है [2]।"]
    assert lang.is_gap("दस्तावेज़ों में यह नहीं बताया गया कि CEO का वेतन कितना है।")
    assert lang.is_gap("The documents do not state the CEO's salary.")
    assert lang.not_found("hi")[0].startswith("इन दस्तावेज़ों")


def test_hindi_greeting_gets_a_hindi_reply():
    from engine.commands import quick_reply
    r = quick_reply("नमस्ते", [{"id": "a", "name": "a.pdf"}])
    assert r["title"] == "नमस्ते, मैं Vyum हूँ" and "दस्तावेज़ों" in r["note"]
    assert quick_reply("dhanyavad", [{"id": "a", "name": "a.pdf"}])["title"] == "You're welcome"


def test_kruti_dev_text_is_recognised():
    kruti = ("Hkkjr ljdkj dk ;g vkns'k gS fd lHkh deZpkfj;ksa dks le; ij dk;kZy; vkuk gksxk A bl laca/k esa fdlh Hkh izdkj "
             "dh fkdk;r gksus ij foHkkx ls laidZ djsa vkSj vkosnu i= Hkjsa A ;g fu;e rqjar ykxw gksxk vkSj lHkh dks bldk ikyu djuk gksxk A")
    assert lang.looks_like_kruti_dev(kruti)
    assert not lang.looks_like_kruti_dev("The Buyer shall pay each invoice within forty-five days of the invoice date. " * 4)


def test_loose_vowel_signs_are_rejoined():
    assert lang.fix_devanagari("मैनेजर की म ं ज़ूरी") == "मैनेजर की मं ज़ूरी"
    assert lang.fix_devanagari("plain English stays") == "plain English stays"


def test_reversed_limits_are_corrected_from_the_source():
    src = "Those obligations shall not apply to an enterprise or an organisation employing fewer than 250 persons."
    fixed, fixes = lang.fix_limits("GDPR के तहत, कम से कम 250 कर्मचारी वाले संगठनों को छूट मिलती है [2]।", src, "hi")
    assert "250 से कम" in fixed and "कम से कम" not in fixed and fixes
    ok, none = lang.fix_limits("250 से कम कर्मचारियों वाले संगठनों को छूट मिलती है [2]।", src, "hi")
    assert not none and "250 से कम" in ok
    en, fx = lang.fix_limits("Organisations with at least 250 employees are exempt [1].", src, "en")
    assert en == "Organisations with fewer than 250 employees are exempt [1]." and fx
    hl, _ = lang.fix_limits("Kam se kam 250 employees wali companies exempt hain [1].", src, "hinglish")
    assert "250 se kam" in hl
    # the source says nothing about a limit on 45: nothing changes
    same, nothing = lang.fix_limits("भुगतान 45 दिन के अंदर करना होगा [1]।", "The Buyer shall pay within forty-five (45) days.", "hi")
    assert not nothing


def test_repeated_facts_from_document_copies_are_said_once():
    from engine.harness import _merge_repeats
    out = _merge_repeats([{"text": "हर कर्मचारी को 24 दिन का सवेतन वार्षिक अवकाश मिलेगा [1]।"},
                          {"text": "24 दिन का सवेतन वार्षिक अवकाश मिलेगा [2]।"},
                          {"text": "अवकाश के लिए 7 दिन पहले आवेदन करना होगा [1]।"}])
    assert [x["text"] for x in out] == ["हर कर्मचारी को 24 दिन का सवेतन वार्षिक अवकाश मिलेगा [1][2]।",
                                        "अवकाश के लिए 7 दिन पहले आवेदन करना होगा [1]।"]
