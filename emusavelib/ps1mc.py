"""ctypes-based classes for working with PS1 memory cards.

Developed using information from http://www.psdevwiki.com/ps3/Game_Saves
"""

from ctypes import Structure, Union, sizeof, c_ubyte, c_char
import logging

log = logging.getLogger(__name__)


class DirectoryFrame(Structure):
    """Frame that contains bookkeeping data for save blocks."""

    BLOCK_AVAILABLE = 0xA0
    BLOCK_UNUSABLE = 0xFF
    BLOCK_FIRST = 0x51
    BLOCK_MIDDLE = 0x52
    BLOCK_LAST = 0x53

    _BLOCK_TYPE_REPR = {
        BLOCK_AVAILABLE: 'free',
        BLOCK_UNUSABLE: 'unusable',
        BLOCK_FIRST: 'first',
        BLOCK_MIDDLE: 'middle',
        BLOCK_LAST: 'last'
    }

    _fields_ = [
        ('block_state', c_ubyte),
        ('reserved', c_ubyte * 3),
        ('use', c_ubyte * 4),
        ('next_block', c_ubyte),
        ('next_frame', c_ubyte),
        ('country_code', c_char * 2),
        ('product_code', c_char * 10),
        ('identifier', c_char * 8),
        ('_padding', c_ubyte * 97),
        ('xor', c_ubyte),
    ]

    def __new__(cls, data=None):
        """Create new instance of a DirectoryFrame.

        If `data` is provided, it is copied into the new instance.
        """
        if data:
            return cls.from_buffer_copy(data)

    def __init__(self, data=None):
        """Initialize a new DirectoryFrame.

        If `data` is not provided, then the frame is created as a new free
        block.
        """
        if data is None:
            self.block_state = self.BLOCK_AVAILABLE
            self.fix_xor()

    def __repr__(self):
        block_type = self._BLOCK_TYPE_REPR[self.block_state]
        if self.block_state in [self.BLOCK_AVAILABLE, self.BLOCK_UNUSABLE]:
            return '<DirectoryFrame: type={type}>'.format(type=block_type)
        return '<DirectoryFrame: type={type} filename="{filename}">'.format(
            type=block_type, filename=self.filename
        )

    def fix_xor(self):
        """Fix the xor field in the DirectoryFrame."""
        self.xor = 0
        other_bytes = bytearray(self)[:-1]
        for byte in other_bytes:
            self.xor = self.xor ^ byte

    @property
    def filename(self):
        """"Filename" for the save.

        The filename is a concatenation of country code, product code, and
        save identifier.
        """
        return "{country_code}{product_code}{identifier}".format(
            country_code=self.country_code.decode('utf-8'),
            product_code=self.product_code.decode('utf-8'),
            identifier=self.identifier.decode('utf-8')
        )


class HeaderBlock(Structure):
    """Structure for working with ps1 memory card header blocks."""

    _fields_ = [
        ('magic', c_char * 2),
        ('_padding0', c_ubyte * 125),
        ('xor', c_ubyte),
        ('directory_frames', DirectoryFrame * 15),
        ('unused_frames', DirectoryFrame * 20),
        ('_filler', c_ubyte * 3456),
        ('_other', c_ubyte * 128),
    ]

    def __new__(cls, data=None):
        """Create a new instance of a HeaderBlock.

        If `data` is provided, copy its contents into the new instance.
        """
        if data:
            assert self.magic == b'MC'
            return cls.from_buffer_copy(data)

    def __init__(self, data=None):
        """Initialize a new header block.

        If `data` is set, asserts a few fixed values are correct, otherwise
        constructs a new memory card structure with 15 blank slots.
        """
        if data is None:
            self.magic = b'MC'
            self.xor = 0xe
            memset(self._filler, 0xff, sizeof(self._filler))
            for frame in self.directory_frames:
                frame.block_state = DirectoryFrame.BLOCK_AVAILABLE
                frame.fix_xor()
            for frame in self.unused_frames:
                frame.block_state = DirectoryFrame.BLOCK_UNUSABLE
                memset(frame.reserved, 0xff, sizeof(frame.reserved))
                memset(frame.link_order, 0xff, sizeof(frame.link_order))


class MetadataBlock(Structure):
    """Block that holds save metadata and some of the actual save data.

    Icon data and save data is intermingled in the `data` field, and it is up
    to the user to separate them if desired.
    """

    _fields_ = [
        ('magic', c_char * 2),
        ('icon_flags', c_ubyte),
        ('save_block_count', c_ubyte),
        ('title', c_char * 64),
        ('_padding0', c_ubyte * 12),
        ('pocketstation_mc_icon_frame_count', c_ubyte * 2),
        ('pocketstation_identifier', c_char * 4),
        ('pocketstation_ap_icon_frame_count', c_ubyte * 2),
        ('_padding1', c_ubyte * 8),
        ('color_pallete', c_ubyte * 32),
        ('data', c_ubyte * (128 * 63)),
    ]

    def __new__(cls, data=None):
        """Create a new instance of a metadata block.

        If `data` is provided, it is copied into the new instance.
        """
        if data:
            return cls.from_buffer_copy(data)

    def __init__(self, data=None):
        """Initialize a new metadata block."""
        pass


class DataBlock(Union):
    """Union for representing data blocks which may contain metadata.

    Data blocks contain metadata when they are the first block used for a save
    game. Subsequent data blocks for the save contain only data.

    If the block is a metadata block, all data should be accessed through the
    `metadata` field (or its fields - `metadata` is exposed as anonymous).
    Otherwise, `raw` should be used to get at the data.
    """
    _anonymous_ = ['metadata']
    _fields_ = [
        ('metadata', MetadataBlock),
        ('raw', c_ubyte * 128 * 64)
    ]


class Save(object):
    """Convenience class for working with a save."""

    def __init__(self, filename, data):
        """Construct a new save with the given filename and data.

        The data should contain the metadata as well.
        """
        self.filename = filename
        self.data = data
        self.metadata = MetadataBlock(data)

    @property
    def title(self):
        """Extract the title from save metadata."""
        return self.metadata.title.decode('shift-jis')

    @property
    def blocks(self):
        """Extract the block count from save metadata."""
        return self.metadata.save_block_count

    def __repr__(self):
        return '<Save: filename="{filename}" blocks={blocks}>'.format(
            filename=self.filename,
            blocks=self.blocks
        )


class MemoryCard(Structure):
    _fields_ = [
        ('header', HeaderBlock),
        ('data', DataBlock * 15)
    ]

    @staticmethod
    def _detect_offset(data):
        magic_bytes = data[:12]
        if magic_bytes[:2] == b'MC':
            log.info('Detected raw memory card')
            return 0
        elif magic_bytes[1:4] == b'PMV':
            log.info('Detected PSP format memory card')
            return 0x80
        elif magic_bytes[0:11] == b'123-456-STD':
            log.info('Detected dexdrive memory card')
            return 0xf40
        else:
            raise IOError('File format not recognized')

    def __new__(cls, data=None):
        if data:
            offset = cls._detect_offset(data)
            return cls.from_buffer_copy(bytes(data)[offset:])

    def __init__(self, data):
        if data is None:
            self.header = HeaderBlock()

    def get_save_at_index(self, first_block_index):
        """Extract a `Save` from the data block at the given index.

        Valid indexes are 0-14
        """
        index = first_block_index
        directory_frames = []
        data = bytearray()
        while index != 0xff:
            directory_frame = self.header.directory_frames[index]
            directory_frames.append(directory_frame)
            data_block = self.data[index]
            data += bytearray(data_block.raw)
            index = directory_frame.next_block
        filename = directory_frames[0].filename
        return Save(filename=filename, data=data)

    def get_saves(self):
        """Extract all saves from the memory card."""
        saves = []
        for i, entry in enumerate(self.header.directory_frames):
            if entry.block_state == DirectoryFrame.BLOCK_FIRST:
                saves.append(self.get_save_at_index(i))
        return saves

    def get_slot_saves(self):
        """Extract saves and place them in a dictionary mapping slots to
        saves.

        If a save takes multiple blocks, it is mapped to the first slot.
        """
        slots = {}
        for i, entry in enumerate(self.header.directory_frames):
            if entry.block_state == DirectoryFrame.BLOCK_FIRST:
                slots[i] = self.get_save_at_index(i)
        return slots
