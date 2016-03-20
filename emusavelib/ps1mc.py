"""ctypes-based classes for working with PS1 memory cards.

Developed using information from http://www.psdevwiki.com/ps3/Game_Saves
"""

from ctypes import Structure, LittleEndianStructure, Union
from ctypes import sizeof, memset, addressof, memmove
from ctypes import c_ubyte, c_char, c_ushort, c_int32
import logging

log = logging.getLogger(__name__)


class DirectoryFrame(LittleEndianStructure):
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
        ('save_length', c_int32),
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
        else:
            return super(DirectoryFrame, cls).__new__(cls)

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
        else:
            return super(HeaderBlock, cls).__new__(cls)

    def __init__(self, data=None):
        """Initialize a new header block.

        If `data` is set, asserts a few fixed values are correct, otherwise
        constructs a new memory card structure with 15 blank slots.
        """
        if data is None:
            self.magic = b'MC'
            self.xor = 0xe
            memset(addressof(self._filler), 0xff, sizeof(self._filler))
            for frame in self.directory_frames:
                frame.block_state = DirectoryFrame.BLOCK_AVAILABLE
                frame.fix_xor()
            for frame in self.unused_frames:
                frame.block_state = DirectoryFrame.BLOCK_UNUSABLE
                memset(addressof(frame.reserved), 0xff, sizeof(frame.reserved))
                frame.next_frame = 0xFF
                frame.next_block = 0xFF


class MetadataBlock(LittleEndianStructure):
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
        ('color_palette', c_ushort * 16),
        ('data', c_ubyte * (128 * 63)),
    ]

    def __new__(cls, data=None):
        """Create a new instance of a metadata block.

        If `data` is provided, it is copied into the new instance.
        """
        if data:
            return cls.from_buffer_copy(data)
        else:
            return super(MetadataBlock, cls).__new__(cls)

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

    def is_valid(self):
        """Validate some basic things about a save."""
        magic_bytes = self.metadata.magic
        length = len(buffer(self.data))
        if magic_bytes != b'SC':
            log.info('Wrong magic bytes: {}'.format(magic_bytes))
            return False
        if length % 8192 != 0:
            log.info('Odd size of data; not a multiple of 8192')
            return False
        blocks_based_on_length = int(length / 8192)
        if blocks_based_on_length != self.blocks:
            log.info('Block count wrong in metadata')
            return False
        return True

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
        else:
            return super(MemoryCard, cls).__new__(cls)

    def __init__(self, data):
        if data is None:
            self.header = HeaderBlock()

    def _gather_save(self, first_block_index):
        index = first_block_index
        directory_frames = []
        data_blocks = []
        data = bytearray()
        while index != 0xff:
            directory_frame = self.header.directory_frames[index]
            directory_frames.append(directory_frame)
            data_blocks.append(self.data[index])
            if index == directory_frame.next_block:
                sys.exit(1)
            index = directory_frame.next_block
        return directory_frames, data_blocks

    def get_save_at_index(self, first_block_index):
        """Extract a `Save` from the data block at the given index.

        Valid indexes are 0-14
        """
        data = bytearray()
        directory_frames, data_blocks = self._gather_save(first_block_index)
        for data_block in data_blocks:
            data += bytearray(data_block.raw)
        filename = directory_frames[0].filename
        return Save(filename=filename, data=data)

    def delete_save(self, target):
        # find index by filename
        index = None
        for i, save in self.get_slot_saves().items():
            if save.filename == target.filename:
                index = i
        self.delete_save_at_index(index)

    def delete_save_at_index(self, index):
        """Remove the save at the given index."""
        directory_frames, data_blocks = self._gather_save(index)
        for frame in directory_frames:
            # reset frame to unused state
            memset(addressof(frame), 0, sizeof(frame))
            DirectoryFrame.__init__(frame)

        for data_block in data_blocks:
            # zero out block
            memset(addressof(data_block), 0, sizeof(data_block))

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

    def add_save(self, save):
        # gather free blocks
        free_block_indexes = []
        for i, entry in enumerate(self.header.directory_frames):
            if entry.block_state == DirectoryFrame.BLOCK_AVAILABLE:
                free_block_indexes.append(i)
        if len(free_block_indexes) < save.blocks:
            log.error('Not enough free blocks')
            return
        # parse metadata from filename
        country_code = save.filename[:2]
        product_code = save.filename[2:2+10]
        identifier = save.filename[2+10:]
        for i in range(save.blocks):
            free_block_index = free_block_indexes[i]
            directory_frame = self.header.directory_frames[free_block_index]
            directory_frame.country_code = country_code.encode('utf-8')
            directory_frame.product_code = product_code.encode('utf-8')
            directory_frame.identifier = identifier.encode('utf-8')
            if i == 0:
                directory_frame.save_length = 8192 * save.blocks
                directory_frame.block_state = DirectoryFrame.BLOCK_FIRST
            else:
                directory_frame.save_length = 0
                directory_frame.block_state = DirectoryFrame.BLOCK_MIDDLE
                if i == save.blocks - 1:
                    directory_frame.block_state = DirectoryFrame.BLOCK_LAST
            if i != save.blocks - 1:
                directory_frame.next_block = free_block_indexes[i + 1]
                directory_frame.next_frame = 0
            else:
                directory_frame.next_block = 0xFF
                directory_frame.next_frame = 0xFF
            directory_frame.fix_xor()
            data_block = self.data[free_block_index]
            chunk = save.data[8192 * i:8192 * (i + 1)]
            temp = DataBlock.from_buffer_copy(chunk)
            memmove(addressof(data_block), addressof(temp), 8192)
