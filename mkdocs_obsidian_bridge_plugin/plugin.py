from collections import defaultdict
from functools import partial
import logging
import re
import os
import urllib.parse
from pathlib import Path

import mkdocs.utils
from mkdocs.config import base
from mkdocs.plugins import BasePlugin

from mkdocs.config.defaults import MkDocsConfig
from mkdocs.structure.files import Files as MkDocsFiles


logger = logging.getLogger(f'mkdocs.plugins.{__name__}')
logger.addFilter(mkdocs.utils.warning_filter)


FilenameToPaths = defaultdict[str, list[Path]]


class NoCandidatesError(Exception):
    pass


# class RoamLinkReplacer(object):
#     def __init__(self, base_docs_url, page_url):
#         self.base_docs_url = base_docs_url
#         self.page_url = page_url

#     def simplify(self, filename):
#         ''' ignore - _ and space different, replace .md to '' so it will match .md file,
#         if you want to link to png, make sure you filename contain suffix .png, same for other files
#         but if you want to link to markdown, you don't need suffix .md '''
#         return re.sub(r"[\-_ ]", "", filename.lower()).replace(".md", "")

#     def gfm_anchor(self, title):
#         '''Convert to gfw title / anchor
#         see: https://gist.github.com/asabaylus/3071099#gistcomment-1593627'''
#         if title:
#             title = title.strip().lower()
#             title = re.sub(r'[^\w\u4e00-\u9fff\- ]', "", title)
#             title = re.sub(r' +', "-", title)
#             return title
#         else:
#             return ""

#     def __call__(self, match):
#         # Name of the markdown file
#         whole_link = match.group(0)
#         filename = match.group(2).strip() if match.group(2) else ""
#         title = match.group(3).strip() if match.group(3) else ""
#         format_title = self.gfm_anchor(title)
#         alias = match.group(4).strip('|') if match.group(4) else ""

#         # Absolute URL of the linker
#         abs_linker_url = os.path.dirname(os.path.join(self.base_docs_url, self.page_url))

#         # Find directory URL to target link
#         rel_link_url = ''
#         # Walk through all files in docs directory to find a matching file
#         if filename:
#             if '/' in filename:
#                 if 'http' in filename:  # http or https
#                     rel_link_url = filename
#                 else:
#                     rel_file = filename
#                     if '.' not in filename:  # don't have extension type
#                         rel_file = filename + '.md'

#                     abs_link_url = os.path.dirname(os.path.join(self.base_docs_url, rel_file))
#                     # Constructing relative path from the linker to the link
#                     rel_link_url = os.path.join(
#                         os.path.relpath(abs_link_url, abs_linker_url), os.path.basename(rel_file)
#                     )
#                     if title:
#                         rel_link_url = rel_link_url + '#' + format_title
#             else:
#                 for root, dirs, files in os.walk(self.base_docs_url):
#                     for name in files:
#                         # If we have a match, create the relative path from linker to the link
#                         if self.simplify(name) == self.simplify(filename):
#                             # Absolute path to the file we want to link to
#                             abs_link_url = os.path.dirname(os.path.join(root, name))
#                             # Constructing relative path from the linker to the link
#                             rel_link_url = os.path.join(os.path.relpath(abs_link_url, abs_linker_url), name)
#                             if title:
#                                 rel_link_url = rel_link_url + '#' + format_title
#             if rel_link_url == '':
#                 logger.warning(f"RoamLinksPlugin unable to find {filename} in directory {self.base_docs_url}")
#                 return whole_link
#         else:
#             rel_link_url = '#' + format_title

#         # Construct the return link
#         # Windows escapes "\" unintentionally, and it creates incorrect links, so need to replace with "/"
#         rel_link_url = rel_link_url.replace('\\', '/')

#         if filename:
#             if alias:
#                 link = f'[{alias}]({rel_link_url})'
#             else:
#                 link = f'[{filename+title}]({rel_link_url})'
#         else:
#             if alias:
#                 link = f'[{alias}]({rel_link_url})'
#             else:
#                 link = f'[{title}]({rel_link_url})'

#         return link


class ObsidianBridgeConfig(base.Config):
    pass


class ObsidianBridgePlugin(BasePlugin):
    '''
    Plugin to make obsidian or incomplete markdown links work.
    '''

    # from https://help.obsidian.md/Advanced+topics/Accepted+file+formats:
    OBSIDIAN_FORMATS = [
        'md',
        'png', 'jpg', 'jpeg', 'gif', 'bmp', 'svg',
        'mp3', 'webm', 'wav', 'm4a', 'ogg', '3gp', 'flac',
        'mp4', 'webm', 'ogv', 'mov', 'mkv',
        'pdf'
    ]

    def __init__(self):
        self.file_map: FilenameToPaths | None = None

    def on_files(self, files: MkDocsFiles, *, config: MkDocsConfig) -> MkDocsFiles:
        '''Initialize the filename lookup dict if it hasn't already been initialized'''
        if self.file_map is None:
            self.file_map = self.build_file_map(files)
        return files

    def on_page_markdown(self, markdown: str, page, config: MkDocsConfig, files: MkDocsFiles, **kwargs) -> str:
        # Getting the root location of markdown source files
        self.docs_dir = Path(config.docs_dir)

        # Getting the page path that we are linking from
        page_path = Path(page.file.abs_src_path)

        # Look for matches and replace

        markdown = self.process_markdown_links(page_path, markdown)
        markdown = self.process_obsidian_links(page_path, markdown)

        return markdown

    def build_file_map(self, files: MkDocsFiles) -> FilenameToPaths:
        result = defaultdict(list)
        for file in files:
            filepath = Path(file.abs_src_path)
            filename = filepath.name
            result[filename].append(Path(file.abs_src_path))
        return result

    def best_path(self, page_dir: Path, candidates: list[Path]) -> Path:
        '''Return the shortest path from the list of path candidates relatively to the page_dir.'''
        assert page_dir.is_absolute()
        assert len(candidates) > 0
        assert all(c.is_absolute() for c in candidates)

        match len(candidates):
            case 1:
                return Path(os.path.relpath(candidates[0], page_dir))

            case n if n > 1:
                # transform all candidates to paths relative to the page directory
                rel_paths = [Path(os.path.relpath(c, page_dir)) for c in candidates]
                # choose the first shortest relative path
                return min(rel_paths, key=lambda p: len(p.parts))

            case _:
                raise NoCandidatesError()

    def find_best_path(self, link_filepath: Path, page_path: Path) -> Path | None:
        assert page_path.is_absolute()
        assert self.file_map is not None

        # Check if the filename exists in the filename to abs path lookup defaultdict
        if link_filepath.name not in self.file_map:
            # An if-statement is necessary because self.filename_to_abs_path is a
            # defaultdict, so the more pythonic try: except: wouldn't work.
            logger.warning(
                '''[ObsidianBridgePlugin] Unable to find %s in directory %s''',
                link_filepath,
                self.docs_dir,
            )
            return

        page_dir = page_path.parent
        path_candidates = self.file_map[link_filepath.name]
        try:
            return self.best_path(page_dir, path_candidates)
        except NoCandidatesError:
            logger.error('''[ObsidianBridgePlugin] No candidates for filepath '%s' were given.''', link_filepath)
            return

    def process_markdown_links(self, page_path: Path, markdown: str) -> str:
        '''
        Find Markdown links to relative paths and transform them so that paths become valid
        (if possible to find a matching sub-path)
        '''

        assert page_path.is_absolute()

        # For Regex, match groups are:
        #       0: Whole markdown link e.g. [Alt-text](url#head "title")
        #       label: Alt text
        #       link: Full URL e.g. url + hash anchor + title
        #       filepath: Filename e.g. filename.md
        #       fragment: hash anchor e.g. #my-sub-heading-link
        #       title: (image) title in quotation marks
        MARKDOWN_LINK = (
            # part in brackets:
            r'(?:\!\[\]|\[(?P<label>[^\]]+)\])'
            # part in parentheses:
            r'\('
                # link:
                r'(?P<link>'
                    r'(?P<filepath>'
                        r'(?!/)[^)]+\.'
                        # alternate file extensions:
                        fr'''(?:{'|'.join(self.OBSIDIAN_FORMATS)})'''
                    r')'
                    r'(?P<fragment>(?:\#[^)]*?)*)'
                r')'
                # title:
                r'(?P<title>(?:\s+\".*\")*)'
            r'\)'
        )

        return re.sub(
            MARKDOWN_LINK,
            partial(self.replace_markdown_link, page_path),
            markdown,
        )

    def replace_markdown_link(self, page_path: Path, match: re.Match) -> str:
        assert page_path.is_absolute()

        whole_match: str = match[0]
        link_filepath = Path(match['filepath'].strip())  # Relative path from the link

        if (new_path := self.find_best_path(link_filepath, page_path)) is not None:
            # TODO: slugify #fragment?
            new_link = f'''[{
                match['label']
            }]({
                urllib.parse.quote(str(new_path))
            }{
                match['fragment']
            }{
                match['title']
            })'''
            logger.debug(f'{whole_match} ==> {new_link}')
            return new_link
        else:
            return whole_match

    def process_obsidian_links(self, page_path: Path, markdown: str) -> str:
        '''
        Find Obsidian's internal [[links]] and transform them into markdown links unless they are either
        wrapped with some number of backticks (`) or with <pre>/<code> HTML tags.
        '''

        def process_chunk(index: int, chunk: str) -> str:
            '''
            Replace links in regular text or return a chunk as is if it's a code block
            '''

            # Obsidian link pattern. Match groups are:
            #       0: Whole roamlike link e.g. [[filename#title|alias]]
            #       filepath: filename
            #       fragment: #title
            #       fragment_text: title
            #       label: alias
            OBSIDIAN_LINK = (
                r'\[\[(?P<filepath>[^\]#\|]*)(?P<fragment>\#(?P<fragment_text>[^\|\]]+))*(?:\|(?P<label>[^\]]*))*\]\]'
            )

            match index:
                case n if n % 3 == 0:  # if regular text
                    return re.sub(
                        OBSIDIAN_LINK,
                        partial(self.replace_obsidian_link, page_path),
                        chunk,
                    )
                case n if n % 3 == 1:  # if code block
                    return chunk
                case _:
                    return ''

        assert page_path.is_absolute()

        # Let's split the source into regular parts and fenced parts.
        # This gives a list of strings where indices are the following:
        #   n + 0: regular text
        #   n + 1: text in backticks, e.g. ```some code```, or <pre>, or <code>
        #   n + 2: actual number of backtics, e.g. ```
        CODE_BLOCK = re.compile(r'((`+).+?\2|<pre>.+?</pre>|<code>.+?</code>)')
        raw_chunks = re.split(CODE_BLOCK, markdown)

        processed_chunks = [
            process_chunk(i, c)
            for i, c in enumerate(raw_chunks)
            if i % 3 != 2  # skip (n + 2) chunks
        ]

        return ''.join(processed_chunks)  # combine chunks together again

    def replace_obsidian_link(self, page_path: Path, match: re.Match) -> str:
        assert page_path.is_absolute()

        whole_match: str = match[0]
        matched_filepath: str = match['filepath'].strip()

        # TODO: slugify fragment
        if matched_filepath == '':
            return f'''[{
                match['label'] or match['fragment_text'] or ''
            }]({
                match['fragment'] or ''
            })'''
        else:
            link_filepath = Path(matched_filepath)

            new_path = self.find_best_path(link_filepath, page_path)
            # if nothing found, try once again but with ".md" file extension
            if new_path is None:
                new_suffix = link_filepath.suffix + '.md'
                new_path = self.find_best_path(link_filepath.with_suffix(new_suffix), page_path)

            alternative_label: str = matched_filepath + (match['fragment'] or '')
            new_link = f'''[{
                match['label'] or alternative_label
            }]({
                urllib.parse.quote(str(new_path or link_filepath))
            }{
                match['fragment'] or ''
            })'''
            logger.debug(f'{whole_match} ==> {new_link}')
            return new_link

    def process_obsidian_callouts(self, markdown: str) -> str:
        # TODO: implement
        return markdown

    def process_obsidian_comments(self, markdown: str) -> str:
        # TODO: implement
        return markdown
