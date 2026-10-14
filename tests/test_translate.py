"""Translation helpers that run without a model: name protection, glossary hints, citation handling."""
from engine import translate


def test_names_and_codes_must_survive():
    cases = {"Acme Supply may terminate the agreement.": ["Acme Supply"],
             "Under GDPR, Globex Corp and Acme must sign.": ["GDPR", "Globex Corp", "Acme"],
             "The code QX-4471-B was issued.": ["QX-4471-B"],
             "The Acme Agreement is governed by the laws of India.": ["Acme", "India"],
             "The Buyer shall pay.": [], "Employees receive 24 days.": [], "Late Payments are charged.": []}
    assert {t: translate.names(t) for t in cases} == cases


def test_codes_must_be_exact_and_other_names_must_survive_the_back_translation():
    en = "The Acme Agreement is governed by the laws of India."
    assert translate.names_kept(en, "Acme समझौते पर भारत का कानून लागू होता है।")
    assert translate.names_kept(en, "एक्मे समझौते पर भारत का कानून लागू होता है।", back="The laws of India apply to the Acme agreement.")
    assert not translate.names_kept(en, "एक्सीमे समझौते पर भारत का कानून लागू होता है।", back="Indian law applies to the exim agreement.")
    assert not translate.names_kept("Under GDPR, fines apply.", "जीडीपीआर के तहत जुर्माना लगता है।")
    assert translate.names_kept("It is in Article 17 of the GDPR.", "यह GDPR के अनुच्छेद 17 में है।", back="It is in Article 17 of the GDPR.")


def test_limits_keep_their_direction():
    en = "Organisations with fewer than 250 employees are exempt."
    assert translate.limits_kept(en, "250 से कम कर्मचारियों वाले संगठनों को छूट है।", "hi")
    assert translate.limits_kept(en, "250 कर्मचारियों से कम वाले संगठनों को छूट है।", "hi")
    assert not translate.limits_kept(en, "कम से कम 250 कर्मचारियों वाले संगठनों को छूट है।", "hi")
    assert not translate.limits_kept(en, "छूट की सीमा 250 कर्मचारी या उससे कम है।", "hi")
    assert translate.limits_kept("The lower age must not be below 13 years.", "कम उम्र 13 वर्ष से कम नहीं होनी चाहिए।", "hi")


def test_glossary_hint_lists_only_terms_in_the_sentence():
    hint = translate._glossary_hint(["Late payments carry interest of 1.5% per month."], "hi")
    assert "late payments = देर से भुगतान" in hint and "per month = प्रति माह" in hint and "warranty" not in hint
    assert translate._glossary_hint(["Late payments."], "mr") == ""


def test_numbers_must_survive():
    assert translate._keeps("Pay within 45 days at 1.5% per month.", "45 दिन के भीतर, प्रति माह 1.5%")
    assert not translate._keeps("Pay within 45 days.", "चालीस दिन के भीतर")


def test_polish_fixes_repeated_numbers_and_oblique_case():
    assert translate.polish("Acme समझौता पर भारत के कानून लागू होते हैं।", "hi") == "Acme समझौते पर भारत के कानून लागू होते हैं।"
    assert translate.polish("45 (45) दिन के भीतर", "hi") == "45 दिन के भीतर"
    assert translate.polish("Acme समझौता 1 मार्च से शुरू होता है।", "hi") == "Acme समझौता 1 मार्च से शुरू होता है।"


def test_names_from_the_question_and_possessives_are_kept():
    assert translate.question_names("Acme की गोपनीय जानकारी कितने साल तक?") == ["Acme"]
    assert translate.names("Acme's confidential information is kept for 5 years.") == ["Acme"]
    assert translate.names("Zeta must pay.", ("Zeta",)) == ["Zeta"]
    assert not translate.names_kept("Acme's data is kept.", "एक्सीमे का डेटा रखा जाता है।", back="Exim's data is kept.")


def test_spelled_out_codes_go_back_to_english():
    assert translate.unspell("जी. डी. पी. आर. के तहत जुर्माना लागू होता है।", "Under GDPR, fines apply.") == "GDPR के तहत जुर्माना लागू होता है।"
    assert translate.unspell("ए. सी. एम. ई. की गोपनीय जानकारी", "Acme's confidential information") == "Acme की गोपनीय जानकारी"


def test_meaning_words_must_survive():
    assert not translate.qualifiers_kept("Employees receive 24 days of paid annual leave.", "कर्मचारियों को 24 दिनों की वार्षिक छुट्टी मिलती है।", "hi")
    assert translate.qualifiers_kept("Employees receive 24 days of paid annual leave.", "कर्मचारियों को 24 दिन का सवेतन वार्षिक अवकाश मिलता है।", "hi")
    assert not translate.qualifiers_kept("Late payments carry interest of 1.5% per month.", "देर से भुगतान पर 1.5% प्रति माह का ब्याज मिलता है।", "hi")
    assert translate.qualifiers_kept("Late payments carry interest of 1.5% per month.", "देर से भुगतान पर प्रति माह 1.5% ब्याज लगता है।", "hi")
    assert not translate.qualifiers_kept("The cap does not apply to breaches.", "सीमा उल्लंघन पर लागू होती है।", "hi")


def test_limits_with_grouped_numbers_and_spoken_negation():
    assert translate.limits_kept("The fine is up to 20 000 000 EUR.", "जुर्माना 20 000 000 EUR तक हो सकता है।", "hi")
    assert translate.limits_kept("Such lower age is not below 13 years.", "ऐसी कम आयु 13 वर्ष से कम न हो।", "hi")
    assert translate.polish("Articles 25 से 39 तक तक लागू", "hi") == "Articles 25 से 39 तक लागू"
