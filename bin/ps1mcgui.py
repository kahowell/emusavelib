#!/usr/bin/env python
import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Pango, GdkPixbuf, GLib, Gio

from emusavelib import ps1mc
import unicodedata
import sys
import os
from cgi import escape
import struct
import logging


class MemoryCardListStore(Gtk.ListStore):
    def _convert_icon_data(self, save):
        palette = save.metadata.color_palette
        palette_indexed = []
        for palette_color in palette:
            r = (0b0000000000011111 & palette_color) << 0
            g = (0b0000001111100000 & palette_color) >> 5
            b = (0b0111110000000000 & palette_color) >> 10

            # fixme subpar mapping
            r8 = r << 3
            g8 = g << 3
            b8 = b << 3

            if palette_color == 0:
                a8 = 0
            else:
                a8 = 255
            palette_indexed.append(struct.pack('BBBB', r8, g8, b8, a8))
        icon_data = save.metadata.data[:128]
        converted = bytearray()
        for byte in icon_data:
            first_pixel = (0xF0 & byte) >> 4
            second_pixel = 0x0F & byte
            converted += palette_indexed[second_pixel]
            converted += palette_indexed[first_pixel]
        return converted

    def __init__(self):
        # slot, icon, filename, title, num_blocks
        Gtk.ListStore.__init__(self, GdkPixbuf.Pixbuf, str, str, int, str, int)

    def set_memory_card(self, mc):
        self.clear()
        for i, save in enumerate(mc.get_saves()):
            converted_icon_data = self._convert_icon_data(save)
            icon_bytes = GLib.Bytes(converted_icon_data)
            icon = GdkPixbuf.Pixbuf.new_from_bytes(icon_bytes,
                                                   GdkPixbuf.Colorspace.RGB,
                                                   has_alpha=True,
                                                   bits_per_sample=8,
                                                   width=16, height=16,
                                                   rowstride=16 * 4)
            normalized_save_title = unicodedata.normalize('NFKC', save.title)
            save_tooltip = escape(u'{}: {}'.format(save.filename,
                                                   normalized_save_title))
            self.append([
                icon,
                save.filename,
                unicodedata.normalize('NFKC', save.title),
                save.blocks,
                save_tooltip,
                i
            ])
        free_blocks = 15 - sum([save.blocks for save in mc.get_saves()])
        if free_blocks:
            self.append([
                None,
                None,
                '<FREE BLOCKS>',
                free_blocks,
                None,
                None,
            ])


class MemoryCardView(Gtk.Box):

    def __init__(self):
        Gtk.Box.__init__(self, orientation=Gtk.Orientation.VERTICAL)
        self.label = Gtk.Label('No card loaded yet...')
        scrollable = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.store = MemoryCardListStore()
        self.tree = Gtk.TreeView(self.store)
        self.tree.set_tooltip_column(4)
        icon = Gtk.TreeViewColumn('Icon', Gtk.CellRendererPixbuf(), pixbuf=0)
        filename = Gtk.TreeViewColumn(
            'Filename',
            Gtk.CellRendererText(),
            text=1
        )
        title_renderer = Gtk.CellRendererText()
        title_renderer.props.ellipsize = Pango.EllipsizeMode.END
        title = Gtk.TreeViewColumn('Title', title_renderer, text=2)
        blocks = Gtk.TreeViewColumn('# Blocks', Gtk.CellRendererText(), text=3)
        for column in [icon, filename, title, blocks]:
            column.set_sizing(Gtk.TreeViewColumnSizing.AUTOSIZE)
            column.set_expand(False)
            self.tree.append_column(column)
        title.set_sizing(Gtk.TreeViewColumnSizing.FIXED)
        title.set_expand(True)
        title.set_fixed_width(50)
        scrollable.set_vexpand(True)
        scrollable.set_hexpand(True)
        scrollable.add(self.tree)
        self.add(self.label)
        self.add(scrollable)

    def set_memory_card(self, mc):
        self.store.set_memory_card(mc)

    def set_filename(self, filename):
        self.label.set_text(filename)


class McToolWindow(Gtk.ApplicationWindow):

    def __init__(self, *args, **kwargs):
        super(McToolWindow, self).__init__(
            title='PS1 Memory Card Tool',
            *args,
            **kwargs
        )
        self.set_border_width(6)
        self.set_default_size(600, 450)
        layout = Gtk.Box(spacing=6, orientation=Gtk.Orientation.VERTICAL)

        toolbar = Gtk.Toolbar()
        toolbar.set_style(Gtk.ToolbarStyle.BOTH)
        layout.add(toolbar)

        # save window
        self.mc_view = MemoryCardView()
        layout.add(self.mc_view)

        # icons
        load_icon = Gtk.Image.new_from_icon_name('folder-open', 16)
        save_icon = Gtk.Image.new_from_icon_name('document-save', 16)
        import_icon = Gtk.Image.new_from_icon_name('document-open', 16)
        export_icon = Gtk.Image.new_from_icon_name('document-send', 16)
        delete_icon = Gtk.Image.new_from_icon_name('edit-delete', 16)

        load = Gtk.ToolButton(icon_widget=load_icon, label='Load Card...')
        load.connect('clicked', self.load_card)

        save = Gtk.ToolButton(icon_widget=save_icon, label='Save Card...')
        save.connect('clicked', self.save_card)

        import_save = Gtk.ToolButton(icon_widget=import_icon,
                                     label='Import Save...')
        import_save.connect('clicked', self.import_save)

        export_save = Gtk.ToolButton(icon_widget=export_icon,
                                     label='Export Save...')
        export_save.connect('clicked', self.export_save)

        delete_save = Gtk.ToolButton(icon_widget=delete_icon,
                                     label='Delete Save...')
        delete_save.connect('clicked', self.delete_save)

        for button in [load, save, import_save, export_save, delete_save]:
            toolbar.insert(button, -1)
        self.add(layout)
        self.show_all()

    def load_card(self, widget):
        file_chooser_buttons = (
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_OPEN, Gtk.ResponseType.OK
        )
        dialog = Gtk.FileChooserDialog(title='Choose a memory card file',
                                       parent=self,
                                       action=Gtk.FileChooserAction.OPEN,
                                       buttons=file_chooser_buttons)
        filter_mc = Gtk.FileFilter()
        filter_mc.set_name('Memory Card Files')
        for extension in ['mcr', 'gme', 'vmp']:
            filter_mc.add_pattern('*.{}'.format(extension.upper()))
            filter_mc.add_pattern('*.{}'.format(extension))
        dialog.add_filter(filter_mc)

        response = dialog.run()
        if response == Gtk.ResponseType.OK:
            self.path = dialog.get_filename()
            self.mc = ps1mc.MemoryCard(open(self.path, 'rb').read())
            self.mc_view.set_memory_card(self.mc)
            self.mc_view.set_filename(self.path)
        dialog.destroy()

    def save_card(self, widget):
        file_chooser_buttons = (
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_SAVE, Gtk.ResponseType.OK
        )
        dialog = Gtk.FileChooserDialog(title='Save memory card as',
                                       parent=self,
                                       action=Gtk.FileChooserAction.SAVE,
                                       buttons=file_chooser_buttons)
        dialog.set_filename(self.path)
        filter_mc = Gtk.FileFilter()
        filter_mc.set_name('Memory Card Files')
        for extension in ['mcr', 'gme', 'vmp']:
            filter_mc.add_pattern('*.{}'.format(extension.upper()))
            filter_mc.add_pattern('*.{}'.format(extension))
        dialog.add_filter(filter_mc)

        response = dialog.run()
        if response == Gtk.ResponseType.OK:
            self.path = dialog.get_filename()
            with open(self.path, 'wb') as output:
                output.write(buffer(self.mc))
            self.mc_view.set_filename(self.path)
        dialog.destroy()

    def import_save(self, widget):
        file_chooser_buttons = (
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_OPEN, Gtk.ResponseType.OK
        )
        dialog = Gtk.FileChooserDialog(title='Choose a raw save file',
                                       parent=self,
                                       action=Gtk.FileChooserAction.OPEN,
                                       buttons=file_chooser_buttons)

        response = dialog.run()
        if response == Gtk.ResponseType.OK:
            filename = dialog.get_filename()
            save_data = open(filename, 'rb').read()
            filename = os.path.basename(filename)
            save = ps1mc.Save(filename=filename, data=save_data)
            if save.is_valid():
                self.mc.add_save(save)
                self.mc_view.set_memory_card(self.mc)
        dialog.destroy()

    def _export_dialog(self, save):
        file_chooser_buttons = (
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_SAVE, Gtk.ResponseType.OK
        )
        dialog = Gtk.FileChooserDialog(title='Export save game as',
                                       parent=self,
                                       action=Gtk.FileChooserAction.SAVE,
                                       buttons=file_chooser_buttons)
        dialog.set_current_name(save.filename)

        response = dialog.run()
        if response == Gtk.ResponseType.OK:
            self.path = dialog.get_filename()
            with open(self.path, 'wb') as output:
                output.write(buffer(save.data))
        dialog.destroy()

    def export_save(self, widget):
        store, save = self.mc_view.tree.get_selection().get_selected()
        if save is not None:
            save_index = store[save][-1]
            if save_index is not None:
                save = self.mc.get_saves()[save_index]
                self._export_dialog(save)

    def delete_save(self, widget):
        store, save = self.mc_view.tree.get_selection().get_selected()
        if save is not None:
            save_index = store[save][-1]
            if save_index is not None:
                self.mc.delete_save(self.mc.get_saves()[save_index])
                self.mc_view.set_memory_card(self.mc)


class McTool(Gtk.Application):

    def __init__(self, *args, **kwargs):
        super(McTool, self).__init__(application_id='net.kahowell.ps1mc',
                                     *args,
                                     **kwargs)
        self.window = None

    def do_startup(self):
        Gtk.Application.do_startup(self)

    def do_activate(self):
        if not self.window:
            self.window = McToolWindow(application=self)
        self.window.present()

if __name__ == '__main__':
    logging.basicConfig()
    logging.getLogger().setLevel(logging.INFO)
    app = McTool()
    app.run(sys.argv)
