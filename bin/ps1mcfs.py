#!/usr/bin/env python
"""Fuse filesystem that exposes a PS1 memory card as a filesystem.

Exposes by filename and by slot. If mounted at /mc for example, expect:
 - /mc/by-filename/{filename}
 - /mc/by-slot/{01-15}/{filename}
"""

from emusavelib import ps1mc
import llfuse
import os
import glob
import stat
import errno
from llfuse import FUSEError, ROOT_INODE, EntryAttributes
import logging

log = logging.getLogger(__name__)


class AttributeFactory:
    defaults = {}
    file_defaults = {}
    directory_defaults = {}

    @classmethod
    def _base_attributes(cls):
        entry = EntryAttributes()
        entry.generation = 0
        entry.entry_timeout = 300
        entry.attr_timeout = 300
        entry.st_rdev = 0
        entry.st_blksize = 8192
        entry.st_nlink = 0
        entry.st_gid = os.getgid()
        entry.st_uid = os.getuid()
        for key, value in cls.defaults.items():
            setattr(entry, key, value)
        return entry

    @classmethod
    def file_attributes(cls):
        entry = cls._base_attributes()
        entry.st_mode = (stat.S_IFREG | 0o444)
        for key, value in cls.file_defaults.items():
            setattr(entry, key, value)
        return entry

    @classmethod
    def directory_attributes(cls):
        entry = cls._base_attributes()
        entry.st_mode = (stat.S_IFDIR | 0o555)
        entry.st_size = 0
        entry.st_blocks = 0
        for key, value in cls.directory_defaults.items():
            setattr(entry, key, value)
        return entry


class InodeGenerator(object):
    current_inode = 0

    @classmethod
    def generate_inode(cls):
        generated = cls.current_inode
        cls.current_inode += 1
        return generated


class BaseEntry(object):
    def lookup(self, name):
        raise FUSEError(errno.ENOENT)

    def readdir(self, offset):
        pass

    def getattr(self):
        raise FUSEError(errno.ENOENT)

    def read(self, offset, size):
        raise FUSEError(errno.ENOTSUP)


class DirEntry(BaseEntry):
    def __init__(self, entries, parent, inode_map):
        self.parent = parent
        if self.parent is None:
            self.parent = self  # root node special case
        self.entries = [
            ('.', self),
            ('..', self.parent)
        ]
        self.entries.extend(entries)
        self.entry_map = dict(entries)
        self.attr = AttributeFactory.directory_attributes()
        self.attr.st_ino = InodeGenerator.generate_inode()
        inode_map[self.attr.st_ino] = self

    def readdir(self, offset):
        if offset < len(self.entries):
            name, entry = self.entries[offset]
            yield (name.encode('utf-8'), entry.getattr(), offset + 1)

    def getattr(self):
        return self.attr

    def lookup(self, name):
        name = name.decode('utf-8')
        if name in self.entry_map:
            return self.entry_map[name].getattr()
        else:
            return super(DirEntry, self).lookup(name)


class SaveFile(BaseEntry):
    def __init__(self, save, inode_map):
        self.save = save
        self.attr = AttributeFactory.file_attributes()
        self.attr.st_ino = InodeGenerator.generate_inode()
        self.attr.st_size = 8192 * save.blocks
        self.attr.st_blocks = int(save.blocks * (8192 / 512))
        inode_map[self.attr.st_ino] = self

    def getattr(self):
        return self.attr

    def read(self, offset, size):
        return bytes(self.save.data)[offset:offset + size]


class SaveEntry(DirEntry):
    def __init__(self, save, parent, inode_map):
        super(SaveEntry, self).__init__(entries=[
            (save.filename, SaveFile(save, inode_map=inode_map))
        ], parent=parent, inode_map=inode_map)


class SlotsEntry(DirEntry):
    def __init__(self, mc, parent, inode_map):
        super(SlotsEntry, self).__init__(entries=[
            (str(index).zfill(2), SaveEntry(save, parent=self,
                                            inode_map=inode_map))
            for index, save in mc.get_slot_saves().items()
        ], parent=parent, inode_map=inode_map)


class SaveFilenameEntry(DirEntry):
    def __init__(self, mc, parent, inode_map):
        super(SaveFilenameEntry, self).__init__(entries=[
            (save.filename, SaveFile(save, inode_map=inode_map))
            for save in mc.get_saves()
        ], parent=parent, inode_map=inode_map)


class McEntry(DirEntry):
    def __init__(self, mc, inode_map):
        super(McEntry, self).__init__(entries=[
            ('by-slot', SlotsEntry(mc, parent=self, inode_map=inode_map)),
            ('by-filename', SaveFilenameEntry(mc, parent=self,
                                              inode_map=inode_map))
        ], parent=None, inode_map=inode_map)


class McFs(llfuse.Operations):

    def __init__(self, path):
        super(McFs, self).__init__()
        path = os.path.expanduser(path)
        with open(path, 'rb') as mc_file:
            memory_card = ps1mc.MemoryCard(mc_file.read())
        root_file_stat = os.stat(path)
        AttributeFactory.defaults['st_atime'] = root_file_stat.st_atime
        AttributeFactory.defaults['st_mtime'] = root_file_stat.st_mtime
        AttributeFactory.defaults['st_ctime'] = root_file_stat.st_ctime
        InodeGenerator.current_inode = ROOT_INODE + 1
        self.inode_map = {}

        mcEntry = McEntry(memory_card, self.inode_map)

        self.inode_map[ROOT_INODE] = mcEntry
        log.debug('inodes: {}'.format(self.inode_map))

    def getattr(self, inode, ctx=None):
        log.debug('getattr {}'.format(inode))
        return self.inode_map.get(inode).getattr()

    def lookup(self, parent_inode, name, ctx=None):
        log.debug('lookup {} {}'.format(parent_inode, name))
        if parent_inode not in self.inode_map:
            raise FUSEError(errno.ENOENT)
        return self.inode_map[parent_inode].lookup(name)

    def opendir(self, inode, ctx=None):
        log.debug('opendir {}'.format(inode))
        if inode not in self.inode_map:
            raise FUSEError(errno.ENOENT)
        if not (self.getattr(inode).st_mode & stat.S_IFDIR):
            raise FUSEError(errno.ENOTDIR)
        return inode

    def readdir(self, fh, offset):
        log.debug('readdir {} {}'.format(fh, offset))
        return self.inode_map[fh].readdir(offset)

    def open(self, inode, flags, ctx=None):
        log.debug('open {}'.format(inode))
        if inode not in self.inode_map:
            raise FUSEError(errno.ENOENT)
        if flags & os.O_RDWR or flags & os.O_WRONLY:
            raise FUSEError(errno.EPERM)  # FIXME writes not supported
        return inode

    def read(self, fh, offset, size):
        log.debug('read {} {} {}'.format(fh, offset, size))
        return self.inode_map[fh].read(offset, size)


def main():
    try:
        llfuse.main()
    finally:
        llfuse.close()

if __name__ == '__main__':
    from argparse import ArgumentParser, RawTextHelpFormatter

    parser = ArgumentParser(description=__doc__,
                            formatter_class=RawTextHelpFormatter)
    parser.add_argument('source', help='path to a memory card file')
    parser.add_argument('directory', help='where to mount')
    parser.add_argument('-f', '--foreground', action='store_true',
                        help='run in foreground')
    parser.add_argument('-d', '--debug', action='store_true',
                        help='enable debug logging')

    args = parser.parse_args()

    if args.debug:
        logging.basicConfig(level=logging.DEBUG)

    fs = McFs(args.source)
    llfuse.init(fs, args.directory, [])

    if args.foreground:
        main()
    else:
        import daemon
        try:
            import systemd.journal
            log.addHandler(systemd.journal.JournalHandler())
        except:
            log.error('Unable to install systemd logging...')

        # monkey patch daemon to not close files on us :-)
        daemon.daemon.close_all_open_files = lambda exclude: None
        with daemon.DaemonContext():
            main()
