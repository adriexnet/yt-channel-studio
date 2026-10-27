"""Publish kit limits (enforced in code, whatever the model returns) and the Upload-Post form."""
import pytest
import studio.pipeline as P

LONG_TITLE = "Este es un título larguísimo que claramente supera el límite de cien caracteres que YouTube permite en Shorts hoy"
RAW = {
    "virality": {"overall": 72, "verdict": "Buen gancho"},
    "on_screen_hooks": ["¿Tu perro hace esto?", "Nadie te lo dijo", "Mira hasta el final", "extra"],
    "youtube": {"title": LONG_TITLE + " #shorts", "title_variants": ["A", "B", "C"],
                "description": "Primera línea gancho.\nContexto. #perros\nSuscríbete.",
                "hashtags": ["#Shorts", "perros fieles", "#perros fieles", "#mascotas", "#extra"],
                "tags": [f"busqueda numero {i} sobre perros" for i in range(60)]},
    "tiktok": {"caption": "Gancho fuerte aquí #oops y más texto", "hashtags": [f"#tag{i}" for i in range(9)]},
    "instagram": {"caption": "x" * 2500, "hashtags": [f"#ig{i}" for i in range(12)] + ["#ig1"]},
    "facebook": {"caption": "¿Lo sabías? Comenta.", "hashtags": ["#a", "#b"]},
    "seed_comments": ["c1", "c2", "c3", "c4"], "teaser": "Mañana sale algo que no esperas",
}


@pytest.fixture
def kit():
    return P.normalize_kit(RAW)


def test_youtube_limits(kit):
    yt = kit["youtube"]
    assert len(yt["title"]) <= 100 and yt["title"].endswith("…") and "#" not in yt["title"]
    assert yt["description"].endswith("#perrosfieles #mascotas #Shorts") and yt["description"].count("#Shorts") == 1
    assert "#perros " not in yt["description"].split("\n\n")[0]
    assert 0 < len(yt["tags"]) <= 500
    assert len(yt["title_variants"]) == 2


def test_caption_limits(kit):
    assert kit["tiktok"]["caption"].count("#tag") == 5 and "#oops" not in kit["tiktok"]["caption"]
    assert kit["instagram"]["caption"].count("#ig") == 5 and len(kit["instagram"]["caption"]) <= 2200
    assert len(kit["seed_comments"]) == 3 and len(kit["on_screen_hooks"]) == 3


def test_markdown_copy(kit):
    md = P.kit_markdown(kit)
    assert all(x in md for x in ("YouTube Shorts", "TikTok", "Instagram Reels", "Facebook Reels", "Comentarios semilla"))


def test_kit_prompt_language():
    assert "Brazilian Portuguese" in P.build_kit_prompt("pt")
    assert "Latin American Spanish" in P.build_kit_prompt("es")


def form_dict(form):
    out = {}
    for key, value in form:
        out.setdefault(key, []).append(value)
    return out


def test_upload_post_form_every_platform(kit, keys):
    keys("upload_post", username="mi_canal", facebook_page_id="123")
    d = form_dict(P.upload_post_form(kit, ["tiktok", "instagram", "youtube", "facebook"], schedule="2026-11-02 18:30",
                                     timezone="America/Costa_Rica", youtube_privacy="unlisted",
                                     tiktok_privacy="SELF_ONLY"))
    assert d["user"] == ["mi_canal"] and d["async_upload"] == ["true"]
    assert d["platform[]"] == ["tiktok", "instagram", "youtube", "facebook"]
    assert d["tiktok_title"] == [kit["tiktok"]["caption"]] and d["instagram_title"] == [kit["instagram"]["caption"]]
    assert d["facebook_description"] == [kit["facebook"]["caption"]] and d["youtube_title"] == [kit["youtube"]["title"]]
    assert d["privacyStatus"] == ["unlisted"] and d["containsSyntheticMedia"] == ["true"]
    assert d["selfDeclaredMadeForKids"] == ["false"] and len(d["tags[]"]) > 3
    assert d["instagram_first_comment"] == ["c1"] == d["youtube_first_comment"] == d["facebook_first_comment"]
    assert d["privacy_level"] == ["SELF_ONLY"] and d["facebook_page_id"] == ["123"]
    assert d["scheduled_date"] == ["2026-11-02T18:30:00"] and d["timezone"] == ["America/Costa_Rica"]


def test_upload_post_form_only_what_is_needed(kit, keys):
    d = dict(P.upload_post_form(kit, ["tiktok"], first_comment=False))
    assert "youtube_title" not in d and "scheduled_date" not in d
    assert not any("first_comment" in k for k in d)


def test_schedule_parsing():
    assert P.parse_schedule("") == ""
    assert P.parse_schedule("2026-11-02T08:05") == "2026-11-02T08:05:00"
    with pytest.raises(RuntimeError):
        P.parse_schedule("mañana")


def test_describe_publish():
    lines = P.describe_publish({"results": {"tiktok": {"success": True, "url": "https://tiktok.com/v/1"},
                                            "instagram": {"success": False, "error": "token expired"}}})
    assert "✅" in lines[0] and "tiktok.com" in lines[0] and "token expired" in lines[1]
    assert P.describe_publish({"results": [{"platform": "youtube", "success": True}]})[0].startswith("✅")
