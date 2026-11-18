from app.albums import group_albums

T0 = 1_763_470_000   # mediodía de un día cualquiera (lejos de la medianoche)


def foto(mid, ts, from_me=True, sender="", text="", quoted=None, kind="image"):
    return {"id": mid, "timestamp": ts, "from_me": from_me, "sender_jid": sender, "text": text,
            "media_type": kind, "quoted_text": quoted}


def test_fotos_seguidas_forman_un_album():
    msgs = [foto("a", T0), foto("b", T0 + 2), foto("c", T0 + 5)]
    assert {k: [m["id"] for m in v] for k, v in group_albums(msgs).items()} == {"a": ["a", "b", "c"]}


def test_una_foto_sola_no_es_album():
    assert group_albums([foto("a", T0), foto("t", T0 + 1, kind="text")]) == {}


def test_corta_por_remitente_tiempo_texto_y_respuesta():
    msgs = [
        foto("a", T0), foto("b", T0 + 1),
        foto("c", T0 + 2, from_me=False, sender="x"), foto("d", T0 + 3, from_me=False, sender="y"),
        foto("e", T0 + 200), foto("f", T0 + 201),
        foto("t", T0 + 202, kind="text"),
        foto("g", T0 + 203), foto("h", T0 + 204, quoted="hola"),
    ]
    got = {k: [m["id"] for m in v] for k, v in group_albums(msgs).items()}
    assert got == {"a": ["a", "b"], "e": ["e", "f"]}
