# -*- coding: utf-8 -*-
"""中英文系列名稱對照字典：給遊戲附加搜尋別名

當遊戲標題（中文）包含 key 時，會附加 aliases 到搜尋欄位，
讓使用者用英文 / 中文 / 日文簡稱都能搜到。
"""

# 每筆：(中文識別字串 list, 額外別名 list)
SERIES = [
    # Nintendo first-party
    (["瑪利歐", "馬力歐", "瑪莉歐"], ["Mario", "マリオ"]),
    (["薩爾達", "塞爾達"], ["Zelda", "The Legend of Zelda", "ゼルダ"]),
    (["寶可夢", "神奇寶貝"], ["Pokemon", "Pokémon", "ポケモン"]),
    (["密特羅德"], ["Metroid", "メトロイド"]),
    (["斯普拉遁", "Splatoon"], ["Splatoon", "スプラトゥーン", "漆彈"]),
    (["集合啦！動物森友會", "動物森友會", "動物之森"], ["Animal Crossing", "動森", "あつ森"]),
    (["任天堂明星大亂鬥", "大亂鬥"], ["Super Smash Bros", "Smash", "スマブラ"]),
    (["聖火降魔錄"], ["Fire Emblem", "ファイアーエムブレム"]),
    (["星之卡比", "卡比"], ["Kirby", "カービィ"]),
    (["大金剛"], ["Donkey Kong", "ドンキーコング", "DK"]),
    (["耀西"], ["Yoshi", "ヨッシー"]),
    (["星戰火狐"], ["Star Fox", "スターフォックス"]),
    (["路易吉洋樓"], ["Luigi", "Luigi's Mansion"]),
    (["王國之心"], ["Kingdom Hearts", "KH"]),
    (["異度神劍"], ["Xenoblade", "ゼノブレイド"]),
    (["太空戰士", "Final Fantasy"], ["Final Fantasy", "FF", "ファイナルファンタジー"]),
    (["勇者鬥惡龍"], ["Dragon Quest", "DQ", "ドラクエ"]),
    (["女神異聞錄"], ["Persona", "ペルソナ"]),
    (["真‧女神轉生", "真女神轉生"], ["Shin Megami Tensei", "SMT"]),
    (["俠盜獵車手"], ["Grand Theft Auto", "GTA"]),
    (["黑暗靈魂"], ["Dark Souls", "ダークソウル"]),
    (["艾爾登法環"], ["Elden Ring", "エルデンリング"]),
    (["惡魔獵人"], ["Devil May Cry", "DMC"]),
    (["惡靈古堡", "生化危機"], ["Resident Evil", "Biohazard", "バイオハザード"]),
    (["快打旋風"], ["Street Fighter", "ストリートファイター"]),
    (["鐵拳"], ["Tekken", "鉄拳"]),
    (["真人快打"], ["Mortal Kombat", "MK"]),
    (["音速小子"], ["Sonic", "ソニック"]),
    (["當個創世神", "我的世界"], ["Minecraft", "マインクラフト"]),
    (["特技摩托車賽"], ["Trials"]),
    (["極限競速"], ["Forza"]),
    (["地平線"], ["Horizon"]),
    (["最後生還者"], ["The Last of Us", "TLoU"]),
    (["神祕海域"], ["Uncharted"]),
    (["決勝時刻"], ["Call of Duty", "COD"]),
    (["戰地風雲"], ["Battlefield", "BF"]),
    (["刺客教條"], ["Assassin's Creed", "AC"]),
    (["看門狗"], ["Watch Dogs"]),
    (["巫師"], ["The Witcher"]),
    (["碧血狂殺"], ["Red Dead Redemption", "RDR"]),
    (["俠盜列車手"], ["Grand Theft Auto", "GTA"]),
    (["人中之龍"], ["Yakuza", "Like a Dragon", "龍が如く"]),
    (["惡魔城"], ["Castlevania", "悪魔城"]),
    (["魔物獵人"], ["Monster Hunter", "MH", "モンスターハンター"]),
    (["靈魂駭客"], ["Soul Hackers"]),
    (["櫻花大戰"], ["Sakura Wars", "サクラ大戦"]),
    (["伊蘇"], ["Ys", "イース"]),
    (["軌跡"], ["Trails", "Kiseki", "軌跡"]),
    (["哆啦A夢", "多啦A夢"], ["Doraemon", "ドラえもん"]),
    (["蠟筆小新"], ["Crayon Shin-chan", "クレヨンしんちゃん"]),
    (["航海王", "海賊王"], ["One Piece", "ワンピース"]),
    (["七龍珠"], ["Dragon Ball", "ドラゴンボール"]),
    (["火影忍者"], ["Naruto", "ナルト"]),
    (["死神"], ["Bleach", "BLEACH"]),
    (["獵人"], ["Hunter x Hunter", "HxH"]),
    (["鬼滅之刃"], ["Demon Slayer", "Kimetsu no Yaiba", "鬼滅"]),
    (["進擊的巨人"], ["Attack on Titan", "AoT", "進撃の巨人"]),
    (["咒術迴戰"], ["Jujutsu Kaisen", "JJK", "呪術廻戦"]),
    (["我的英雄學院"], ["My Hero Academia", "MHA"]),
    (["數碼寶貝"], ["Digimon", "デジモン"]),
    (["勇者鬥惡龍"], ["Dragon Quest", "DQ", "ドラクエ"]),
]


def enrich_aliases(zh_name: str) -> list[str]:
    """根據中文名稱回傳該遊戲附加的搜尋別名"""
    aliases = set()
    for keys, extras in SERIES:
        if any(k in zh_name for k in keys):
            aliases.update(keys)
            aliases.update(extras)
    return sorted(aliases)
