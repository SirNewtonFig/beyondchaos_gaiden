from randomtools.tablereader import (
    TableObject, get_global_label, tblpath, addresses, get_random_degree,
    mutate_normal, shuffle_normal)
from randomtools.utils import (
    classproperty, cached_property, get_snes_palette_transformer,
    read_multi, write_multi, utilrandom as random)
from randomtools.interface import (
    get_outfile, get_seed, get_flags, get_activated_codes,
    run_interface, rewrite_snes_meta, clean_and_write, finish_interface)
from collections import defaultdict
from os import path
from time import time, sleep
from collections import Counter


VERSION = 0
ALL_OBJECTS = None
DEBUG_MODE = False

price_message_indexes = {
    10:     0xa6b,
    50:     0xa6c,
    100:    0xa6d,
    500:    0xa6e,
    1000:   0xa6f,
    1500:   0xa70,
    2000:   0xa71,
    3000:   0xa72,
    4000:   0xa73,
    5000:   0xa74,
    7000:   0xa75,
    8000:   0xa5f,
    10000:  0xa63,
    12000:  0xa60,
    15000:  0xa61,
    18000:  0xa62,
    20000:  0xa67,
    30000:  0xa64,
    40000:  0xa65,
    50000:  0xa68,
    60000:  0xa66,
    }


def int_to_bytelist(value, length):
    value_list = []
    for _ in xrange(length):
        value_list.append(value & 0xFF)
        value >>= 8
    assert value == 0
    return value_list


class InitialMembitObject(TableObject): pass
class CharPaletteObject(TableObject): pass
class EventObject(TableObject): pass

class NpcObject(TableObject):
    done_pay_saves = {}

    @property
    def event_addr(self):
        return self.misc & 0x3FFFF

    @property
    def palette(self):
        return (self.misc >> 18) & 7

    @property
    def membit(self):
        return self.misc >> 22

    def set_event_addr(self, event_addr):
        self.misc |= 0x3FFFF
        self.misc ^= 0x3FFFF
        self.misc |= event_addr

    def set_palette(self, palette_index):
        mask = 0x7 << 18
        self.misc |= mask
        self.misc ^= mask
        self.misc |= (palette_index << 18)
        assert self.palette == palette_index

    def set_membit(self, membit):
        mask = 0x3FF << 22
        self.misc |= mask
        self.misc ^= mask
        self.misc |= (membit << 22)
        assert self.membit == membit

    def become_pay_save(self, pointer, price, price_message, pay_save_command,
                        write_event):
        self.graphics = 0x6F
        self.set_palette(6)
        self.facing = 0x43
        if price in self.done_pay_saves:
            self.set_event_addr(self.done_pay_saves[price])
            return

        yes_p = pointer + 13
        no_p = yes_p + 7
        script = [
            0x4B] + int_to_bytelist(price_message, 2) + [   # show price
            0x4B] + int_to_bytelist(addresses.ask_message | 0x8000, 2) + [
            0xB6] + int_to_bytelist(yes_p, 3) + int_to_bytelist(no_p, 3) + [
            0x85] + int_to_bytelist(price, 2)               # take money
        script += pay_save_command + [0xFE]
        assert script[no_p-pointer:] == [0xFE]
        event_addr = write_event(script) - 0xA0000
        self.set_event_addr(event_addr)
        self.done_pay_saves[price] = event_addr


class ShopObject(TableObject):
    @property
    def items(self):
        return [ItemObject.get(i) for i in self.item_ids if i < 0xFF]

    @property
    def shop_type(self):
        shop_types = {1:"weapons", 2:"armor", 3:"items", 4:"relics", 5:"misc"}
        return shop_types[self.misc & 0x7]

    @property
    def rank(self):
        if set(self.item_ids) == {255}:
            return -1
        return max(i.price for i in self.items)


class DialoguePtrObject(TableObject):
    @classmethod
    def bring_back_auction_prices(cls):
        if "BNW" not in get_global_label():
            raise NotImplementedError

        indexes = sorted(price_message_indexes.values())
        assert all([i & 0xa00 == 0xa00 for i in indexes])
        pointer = min([DialoguePtrObject.get(i).dialogue_pointer
                       for i in indexes]) | 0xE0000
        message_head = "\x01\x14\x08"
        message_tail = "\x7f\x26\x2f\x5e\x00"
        reverse_dict = dict([(v, k) for (k, v)
                             in price_message_indexes.items()])
        f = open(get_outfile(), "r+b")
        for i in indexes:
            dpo = DialoguePtrObject.get(i)
            dpo.dialogue_pointer = pointer & 0xFFFF
            value = str(reverse_dict[i])
            content = ""
            for c in value:
                content += chr(0x54 + int(c))
            f.seek(pointer)
            s = message_head + content + message_tail
            f.write(s)
            pointer += len(s)


class MonsterObject(TableObject): pass
class MonsterLootObject(TableObject): pass
class MonsterCtrlObject(TableObject): pass
class MonsterSketchObject(TableObject): pass
class MonsterRageObject(TableObject): pass
class MonsterAIObject(TableObject): pass

class FourPackObject(TableObject):
    @property
    def formations(self):
        return [FormationObject.get(self.common1),
                FormationObject.get(self.common2),
                FormationObject.get(self.common3),
                FormationObject.get(self.rare),
                ]


class TwoPackObject(TableObject):
    @property
    def formations(self):
        return [FormationObject.get(self.common),
                FormationObject.get(self.rare),
                ]


class ZonePackPackObject(TableObject):
    @property
    def packs(self):
        return [FourPackObject.get(pid) for pid in self.pack_ids]

    @property
    def formations(self):
        return [f for pack in self.packs for f in pack.formations]


class AreaPackObject(TableObject):
    @property
    def pack(self):
        return FourPackObject.get(self.pack_id)

    @property
    def formations(self):
        return self.pack.formations


class FormationMetaObject(TableObject): pass

class FormationObject(TableObject):
    @property
    def metadata(self):
        return FormationMetaObject.get(self.index)


class ItemObject(TableObject): pass
class EntranceObject(TableObject): pass
class LocNamePtrObject(TableObject): pass

class ChestObject(TableObject):
    @property
    def memid(self):
        memid = self.memid_low
        if self.get_bit("memid_high"):
            memid |= 0x100
        return memid

    def set_memid(self, index):
        assert index <= 0x1FF
        if self.index & 0x100:
            self.set_bit("memid_high", True)
        else:
            self.set_bit("memid_high", False)
        self.memid_low = index & 0xFF


class LocationObject(TableObject):
    @property
    def events(self):
        return EventObject.getgroup(self.index)

    @property
    def npcs(self):
        return NpcObject.getgroup(self.index)

    @property
    def exits(self):
        return EntranceObject.getgroup(self.index)

    @property
    def long_exits(self):
        return LongEntranceObject.getgroup(self.index)

    @property
    def chests(self):
        return ChestObject.getgroup(self.index)

    @property
    def area_pack(self):
        return AreaPackObject.get(self.index)

    @property
    def pack(self):
        return self.area_pack.pack

    @property
    def formations(self):
        return self.pack.formations

    def purge_associated_objects(self):
        for x in self.exits + self.long_exits:
            x.groupindex = -1
        for e in self.events:
            e.groupindex = -1
        for n in self.npcs:
            n.groupindex = -1

    def set_palette(self, value):
        self.palette_index |= 0x3F
        self.palette_index ^= 0x3F
        self.palette_index |= value


class LongEntranceObject(TableObject): pass

class CharacterObject(TableObject):
    def cleanup(self):
        self.level = 0
        self.relics = [0xDE, 0xE6]
        self.relics = [0xFF, 0xE6]


def number_location_names():
    pointer = addresses.location_names
    f = open(get_outfile(), 'r+b')
    f.seek(pointer)
    f.write('\x00')
    for i in xrange(1, 101):
        pointer = f.tell() - addresses.location_names
        LocNamePtrObject.get(i).name_pointer = pointer
        s = "{0:0>2}".format(i)
        for c in s:
            v = int(c)
            f.write(chr(0x54 + v))
        f.write('\x00')
    assert f.tell() <= addresses.location_names_max
    f.close()


fanatix_space_pointer = None


def execute_fanatix_mode():
    for i in xrange(32):
        InitialMembitObject.get(i).membyte = 0xFF

    BANNED_MAPS = [
        0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x07, 0x0B, 0x0C, 0x0D,
        0x11, 0x14, 0x15, 0x22, 0x2f, 0x37, 0x40, 0x4b, 0x50, 0x53, 0x5b,
        0x75, 0x7b, 0x7d, 0x7e, 0x7f,
        0x81, 0x82, 0x88, 0x89, 0x8c, 0x8f,
        0x90, 0x92, 0x99, 0x9c, 0x9d, 0xa9,
        0xb6, 0xb7, 0xb8, 0xbd, 0xbe,
        0xcd, 0xcf, 0xd0, 0xd1, 0xd9, 0xdd,
        0xd2, 0xd3, 0xd4, 0xd5, 0xd7,
        0xe1, 0xe7, 0xe9, 0xea, 0xeb,
        0xfd, 0xfe, 0xff,
        0x100, 0x102, 0x103, 0x104, 0x105, 0x106, 0x107, 0x10c, 0x12e,
        0x131, 0x132, 0x139, 0x13a, 0x13b, 0x13c, 0x13d, 0x13e,
        0x141, 0x142, 0x143, 0x144,
        0x150, 0x154, 0x155, 0x157, 0x158,
        ]
    BANNED_MAPS += range(0x160, 0x200)
    BANNED_MAPS.remove(0x16a)
    BANNED_MAPS.remove(0x16c)

    for l in LocationObject.every:
        if l.index in BANNED_MAPS:
            continue
        l.name_id = 0
        for x in l.exits:
            if l.index > 2:
                x.groupindex = -1
        for x in l.long_exits:
            if l.index != 0x16a:
                x.groupindex = -1
        for c in l.chests:
            c.groupindex = -1

        for n in l.npcs:
            n.groupindex = -1
        for e in l.events:
            e.groupindex = -1

    number_location_names()

    opening_event = [
        0xB8, 0x42,                         # enable morph
        0xB8, 0x43,                         # show magic points after battle
        0xB8, 0x4B,                         # shadow can't leave
        0x88, 0x00, 0x00, 0x00,             # remove magitek status from terra
        0x3F, 0x00, 0x00,                   # remove terra
        0x3E, 0x00,
        0x3F, 0x0E, 0x00,                   # remove biggs
        0x3E, 0x0E,
        0x3F, 0x0F, 0x00,                   # remove wedge
        0x3E, 0x0F,
        ]
    for i in xrange(0xE):
        opening_event += [
            0x7F, i, i,     # character name
            0x37, i, i,     # character sprite
            0x43, i, CharPaletteObject.get(i).palette_index,
            0x40, i, i,     # character data
            0xD4, 0xE0+i,
            0xD4, 0xF0+i,
            ]

    opening_event += [
        0x3D, 0x00,
        0x3F, 0x00, 0x01,                           # start with terra
        0x84, 0xFF, 0xFF,                           # starting gil
        0x6B, 0x01, 0x20, 160, 127, 0x00, 0xFF,     # start at fanatics tower
        0xFE,
        ]
    f = open(get_outfile(), 'r+b')
    f.seek(addresses.opening_crawl_pointer)
    f.write("".join(map(chr, [0xFD]*4)))  # no opening crawl
    opening_jump_pointer = addresses.opening_jump_pointer
    f.seek(addresses.opening_pointer)
    f.write("".join(map(chr,
        [0xB2] + int_to_bytelist(opening_jump_pointer-0xA0000, 3) + [0xFE])))
    f.seek(opening_jump_pointer)
    f.write("".join(map(chr, opening_event)))

    partydict = {}
    removedict = {}
    done_parties = set([])
    NUM_FLOORS = 99
    #NUM_FLOORS = 49
    #NUM_FLOORS = 2
    next_membit = 1
    LocationObject.class_reseed("prefanatix")
    for n in xrange(NUM_FLOORS):
        if n == 0:
            party = tuple(sorted(random.sample(range(14),5)))
        else:
            party = partydict[n-1]
            for _ in xrange(1000):
                newparty = list(party)
                newchar = random.choice([c for c in range(14)
                                         if c not in party])
                if n >= 2:
                    oldchars = [c for c in party if c in partydict[n-2]]
                else:
                    oldchars = party
                oldchar = random.choice(oldchars)
                newparty.remove(oldchar)
                newparty.append(newchar)
                newparty = tuple(sorted(newparty))
                if newparty not in done_parties:
                    break
            party = newparty
            removedict[n] = oldchar
        partydict[n] = party
        done_parties.add(party)

    limit = addresses.fanatix_space_limit
    def write_event(script):
        global fanatix_space_pointer
        if fanatix_space_pointer is None:
            fanatix_space_pointer = addresses.fanatix_space_pointer
        old_pointer = fanatix_space_pointer
        f.seek(fanatix_space_pointer)
        f.write("".join(map(chr, script)))
        fanatix_space_pointer += len(script)
        assert fanatix_space_pointer <= limit
        return old_pointer

    clear_party_script = []
    clear_party_script += [0x46, 0x01]
    for i in xrange(14):
        clear_party_script += [0x3E, i]
        clear_party_script += [0x3F, i, 0x00]
    clear_party_script += [0xFE]
    clear_party = write_event(clear_party_script) - 0xA0000
    clear_party_command = [0xB2] + int_to_bytelist(clear_party, 3)

    post_boss_script = [
        0xB2] + int_to_bytelist(addresses.gameover_check_pointer-0xA0000,
                                3) + [
        0x3E, 0x10,                         # delete npc
        0x59, 0x08,                         # unfade
        0xFE,
        ]
    post_boss = write_event(post_boss_script) - 0xA0000
    post_boss_command = [0xB2] + int_to_bytelist(post_boss, 3)

    pay_save_script = [
        0xC0, 0xBE, 0x81, 0xFF, 0x69, 0x01,     # check enough money
        0x55, 0x80,                             # flash screen
        0xD2, 0xB5,                             # allow saving
        0xD2, 0xBF,
        0x3A,
        0xFE,
        ]
    pay_save = write_event(pay_save_script) - 0xA0000
    pay_save_command = [0xB2] + int_to_bytelist(pay_save, 3)

    pay_inn_script = [
        0xC0, 0xBE, 0x81, 0xFF, 0x69, 0x01,     # check enough money
        0xB2] + int_to_bytelist(addresses.refreshments_pointer-0xA0000, 3) + [
        0xFE
        ]
    pay_inn = write_event(pay_inn_script) - 0xA0000
    pay_inn_command = [0xB2] + int_to_bytelist(pay_inn, 3)

    done_pay_inns = {}

    esper_floors = random.sample(range(NUM_FLOORS), min(27, NUM_FLOORS))
    esper_floors = dict((b, a) for (a, b) in enumerate(esper_floors))

    colosseum_floor = random.randint(0, NUM_FLOORS-1)

    tower_map = LocationObject.get(0x167)
    tower_base = LocationObject.get(0x16a)
    tower_treasure_room = LocationObject.get(0x16d)
    tower_roof = LocationObject.get(0x16c)
    for l in [tower_base, tower_roof]:
        l.set_palette(16)

    prev = None
    dummy = ChestObject.create_new()
    dummy.groupindex = 0
    next_map = 0
    while next_map in BANNED_MAPS:
        next_map += 1
    for n in xrange(NUM_FLOORS):
        # outside section
        LocationObject.get(n).reseed("fanatix")
        l = LocationObject.get(next_map)
        next_map += 1
        while next_map in BANNED_MAPS:
            next_map += 1
        l.purge_associated_objects()
        l.copy_data(tower_map)
        e = EventObject.create_new()
        e.x, e.y = 8, 1
        e.groupindex = prev.index if prev else tower_base.index

        locked = 0
        num_locked = (random.randint(0, 1) + random.randint(0, 1)
                      + random.randint(0, 1))
        to_lock = random.sample(partydict[n], num_locked)
        script = []

        script += clear_party_command
        for i in partydict[n]:
            script += [0x3D, i]
        if n in removedict:
            script += [0x3D, removedict[n]]
            assert removedict[n] not in partydict[n]

        for i in sorted(to_lock):
            script += [0x3F, i, 0x01]
            locked |= (1 << i)

        suggested = random.sample([
            c for c in partydict[n] if c not in to_lock], 4-len(to_lock))
        for i in sorted(suggested):
            script += [0x3F, i, 0x01]
        assert len(set(suggested + to_lock)) == 4

        for i in xrange(14):
            if i not in partydict[n]:
                locked |= (1 << i)

        script += [
            0x99, 0x01] + int_to_bytelist(locked, 2) + [        # party select
            0x6B] + int_to_bytelist(l.index | 0x1000, 2) + [9, 27, 0x00,
            0xFE,
            ]
        e.event_addr = write_event(script) - 0xA0000

        npc = NpcObject.create_new()
        npc.groupindex = l.index
        npc.graphics = 0x6F
        npc.set_palette(5)
        npc.facing = 0x43
        npc.x, npc.y = 5, 3
        assert len(l.npcs) == 1
        script = [
            #0x4D, 0x00, 0x3F,   # battle
            ]
        script += post_boss_command
        script += [
            0xD7 | ((next_membit >> 8)*2), next_membit & 0xFF,
            0xFE,
            ]
        npc.set_event_addr(write_event(script) - 0xA0000)
        npc.set_membit(next_membit)
        next_membit += 1

        x = EntranceObject.create_new()
        x.groupindex = l.index
        x.dest = (prev.index if prev else tower_base.index) | 0x3000
        x.x, x.y = 7, 29
        x.destx, x.desty = 7, 2

        # inside section
        l2 = LocationObject.get(next_map)
        next_map += 1
        while next_map in BANNED_MAPS:
            next_map += 1
        l2.purge_associated_objects()
        l2.copy_data(tower_treasure_room)
        #l2.set_bit("warpable", True)
        l2.set_bit("enable_encounters", False)
        x = EntranceObject.create_new()
        x.groupindex, x.dest = l.index, l2.index | 0x800
        x.x, x.y = 10, 10
        x.destx, x.desty = 7, 12
        x = EntranceObject.create_new()
        x.groupindex, x.dest = l2.index, l.index | 0x2000
        x.x, x.y = 7, 13
        x.destx, x.desty = 10, 11

        c = ChestObject.create_new()
        c.groupindex = l2.index
        c.x, c.y = 7, 6
        c.set_memid(n+1)
        c.set_bit("treasure", True)
        c.contents = 0

        ratio = min(n / float(NUM_FLOORS-1), 1.0)
        index = int(round((len(price_message_indexes.keys())-1) * ratio))
        price = sorted(price_message_indexes.keys())[index]
        price_message = price_message_indexes[price]

        if n in esper_floors:
            npc = NpcObject.create_new()
            npc.groupindex = l2.index
            npc.graphics = 0x5B
            npc.facing = 0x54
            npc.set_palette(2)
            npc.x, npc.y = 6, 6
            script = [
                0xF4, 0x8D,
                0x86, esper_floors[n] + 0x36,
                0x3E, 0x10,
                0xFE,
                ]
            event_addr = write_event(script) - 0xA0000
            assert len(l2.npcs) == 1
            npc.set_event_addr(event_addr)

        npc = NpcObject.create_new()
        npc.groupindex = l2.index
        npc.facing = 2
        npc.x, npc.y = 4, 8

        if n == colosseum_floor:
            npc_choice = "colosseum"
        else:
            npc_choice = random.choice(["save_point", "inn", "weapon_shop",
                                        "armor_shop", "relic_shop",
                                        "item_shop", "item_shop"])
        if npc_choice == "save_point":
            pointer = fanatix_space_pointer - 0xA0000
            npc.become_pay_save(pointer, price, price_message,
                                pay_save_command, write_event)
        elif npc_choice == "inn":
            npc.graphics = 0x1E
            npc.set_palette(3)
            if price in done_pay_inns:
                npc.set_event_addr(done_pay_inns[price])
            else:
                pointer = fanatix_space_pointer - 0xA0000
                yes_p = pointer + 13
                no_p = yes_p + 7
                script = [
                    0x4B] + int_to_bytelist(price_message, 2) + [   # show $$$
                    0x4B] + int_to_bytelist(addresses.inn_ask_message, 2) + [
                    0xB6] + (int_to_bytelist(yes_p, 3) +
                             int_to_bytelist(no_p, 3)) + [
                    0x85] + int_to_bytelist(price, 2)               # take $$$
                script += pay_inn_command + [0xFE]
                assert script[no_p-pointer:] == [0xFE]
                event_addr = write_event(script) - 0xA0000
                npc.set_event_addr(event_addr)
                done_pay_inns[price] = npc.event_addr
        elif npc_choice == "colosseum":
            npc.graphics = 0x3B
            npc.set_palette(2)
            npc.set_event_addr(addresses.colosseum_pointer - 0xA0000)
        elif "shop" in npc_choice:
            if npc_choice == "weapon_shop":
                npc.graphics = 0x0E
                npc.set_palette(4)
                shops = [s for s in ShopObject.every
                         if s.rank > 0 and s.shop_type == "weapons"]
            elif npc_choice == "armor_shop":
                npc.graphics = 0x0E
                npc.set_palette(3)
                shops = [s for s in ShopObject.every
                         if s.rank > 0 and s.shop_type == "armor"]
            elif npc_choice == "relic_shop":
                npc.graphics = 0x13
                npc.set_palette(0)
                shops = [s for s in ShopObject.every
                         if s.rank > 0 and s.shop_type == "relics"]
            else:
                npc.graphics = 0x36
                npc.set_palette(1)
                shops = [s for s in ShopObject.every
                         if s.rank > 0 and s.shop_type in ["items", "misc"]]
            chosen = random.choice(shops)
            script = [0x9B, chosen.index,
                      0xFE]
            event_addr = write_event(script) - 0xA0000
            npc.set_event_addr(event_addr)

        npc = NpcObject.create_new()
        npc.groupindex = l2.index
        npc.graphics = 0x17
        npc.set_palette(0)
        npc.facing = 2
        npc.x, npc.y = 10, 8
        npc.set_event_addr(addresses.unequipper_pointer - 0xA0000)

        l.name_id, l2.name_id = n+1, n+1
        l.set_bit("enable_encounters", False)
        l.set_palette(16)
        prev = l

    # top section
    LocationObject.class_reseed("postfanatix")
    assert next_membit <= 0x100
    x = EntranceObject.create_new()
    x.groupindex = prev.index
    x.x, x.y = 8, 1
    x.dest = tower_roof.index | 0x1000
    x.destx, x.desty = 8, 13

    x = EntranceObject.create_new()
    x.groupindex = tower_roof.index
    x.x, x.y = 7, 14
    x.dest = prev.index | 0x3000
    x.destx, x.desty = 7, 2

    npc = NpcObject.create_new()
    npc.groupindex = tower_roof.index
    npc.x, npc.y = 4, 5
    pointer = fanatix_space_pointer - 0xA0000
    npc.become_pay_save(pointer, price, price_message,
                        pay_save_command, write_event)


    npc = NpcObject.create_new()
    npc.groupindex = tower_roof.index
    npc.graphics = 0x17
    npc.set_palette(0)
    npc.facing = 2
    npc.x, npc.y = 11, 6
    npc.set_event_addr(addresses.unequipper_pointer-0xA0000)

    final_room = LocationObject.get(0x19b)
    for x in final_room.exits:
        x.groupindex = -1

    e = EventObject.create_new()
    e.x, e.y = 7, 6
    e.groupindex = tower_roof.index
    script = list(clear_party_command)
    script += (
        [0xB2] + int_to_bytelist(addresses.load_all_party_pointer-0xA0000, 3))
    locked = 0
    not_locked = range(14)
    for i in xrange(4):
        num_lock = int(round(random.random() + random.random()
                             + random.random()))
        for _ in xrange(num_lock):
            c = random.choice(not_locked)
            locked |= (1 << c)
            script += [0x3F, c, i]
            not_locked.remove(c)
    script += [
        0x46, 0x02,
        0x99, 0x03] + int_to_bytelist(locked, 2) + [    # party select
        0x6B] + int_to_bytelist(final_room.index, 2) + [
            109, 42, 0x00,      # next map
        0xD2, 0xCE,             # enable party switching with Y
        # place party 3 and select it
        0x79, 0x03] + int_to_bytelist(final_room.index, 2) + [
        0x46, 0x03,
        0x45,
        0x31, 0x84, 0xD5, 115, 44, 0xFF,
        0x47,
        0x41, 0x31,
        0x45,
        # place party 1 and select it
        0x79, 0x01] + int_to_bytelist(final_room.index, 2) + [
        0x46, 0x01,
        0x45,
        0x31, 0x84, 0xD5, 103, 45, 0xFF,
        0x47,
        0x41, 0x31,
        0x45,
        0x46, 0x02,
        0x45,
        0x31, 0x84, 0xD5, 109, 42, 0xFF,
        0x47,
        0x45,
        0xFE,
        ]
    e.event_addr = write_event(script) - 0xA0000

    for x, y in [(103, 49), (109, 46), (115, 48)]:
        ex = EntranceObject.create_new()
        ex.groupindex = final_room.index
        ex.dest = tower_roof.index | 0x2000
        ex.x, ex.y = x, y
        ex.destx, ex.desty = 7, 7

    script = []
    for i, pack_index in enumerate([0, 0, 0]):
        script += [
            0x46, i+1,
            0x4D, pack_index & 0xFF, 0x36,
            0xB2] + int_to_bytelist(addresses.gameover_check_pointer-0xA0000,
                                    3)

    script += [
        0xDC, 0x7E,     # set/clear bits to fix ending
        0xD7, 0x9F,     # clear bit $39F (1EF3-7)
        0xD7, 0xFF,     # clear bit $3FF (1EFF-7)
        0xB2] + int_to_bytelist(addresses.ending_pointer-0xA0000, 3) + [
        0xFE,
        ]
    f.seek(addresses.final_pointer)
    f.write("".join(map(chr, script)))

    if "BNW" in get_global_label():
        DialoguePtrObject.bring_back_auction_prices()
        f.seek(addresses.cheatproof_addr)
        f.write("".join(map(chr,
            [0xB2] + int_to_bytelist(addresses.final_pointer-0xA0000, 3))))

    tower_roof.set_bit("enable_encounters", False)

    f.close()


if __name__ == "__main__":
    try:
        print ("You are using the Beyond Chaos Gaiden "
               "randomizer version %s." % VERSION)
        print

        ALL_OBJECTS = [g for g in globals().values()
                       if isinstance(g, type) and issubclass(g, TableObject)
                       and g not in [TableObject]]

        codes = {
        }
        run_interface(ALL_OBJECTS, snes=True, codes=codes)

        execute_fanatix_mode()

        hexify = lambda x: "{0:0>2}".format("%x" % x)
        numify = lambda x: "{0: >3}".format(x)
        minmax = lambda x: (min(x), max(x))

        clean_and_write(ALL_OBJECTS)
        rewrite_snes_meta("BCG-R", VERSION, lorom=False)

        finish_interface()

    except Exception, e:
        print "ERROR: %s" % e
        raw_input("Press Enter to close this program.")
