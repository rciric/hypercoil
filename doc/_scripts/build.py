# -*- coding: utf-8 -*-
# emacs: -*- mode: python; py-indent-offset: 4; indent-tabs-mode: nil -*-
# vi: set ft=python sts=4 ts=4 sw=4 et:
"""
Build documentation for each library using ``quartodoc``.
"""
import importlib
import inspect
import sys
import warnings
import yaml
from io import StringIO
from pathlib import Path
from shutil import copy, rmtree
from subprocess import run
from typing import List

import quartodoc
import quartodoc.builder
from griffe import (
    Alias,
    Class,
    DocstringSectionText,
    ExprName,
    Function,
    Object,
    ParameterKind,
    Parameters,
)
from plum import dispatch
from quartodoc.__main__ import chdir
from quartodoc.ast import DocstringSectionSeeAlso
from quartodoc.renderers.md_renderer import (
    _has_attr_section,
)


SRCREPO = 'https://github.com/hypercoil/{lib}/tree/main/src/{lib}'
del quartodoc.renderers.Renderer._registry['markdown']
del quartodoc.Builder._registry['pkgdown']


class HBuilder(quartodoc.Builder):
    style: str = 'pkgdown'

    def _sidebar_entry(self, section: List[quartodoc.layout.Section]):
        contents = []
        section, *subsections = section
        # if section.kind == 'page':
        #     assert len(section.contents) == 1, section.contents
        #     return self._page_to_links(section)
            # return {
            #     'section': f'`{section.contents[0].name}`',
            #     'contents': self._page_to_links(section),
            # }

        for entry in section.contents:
            if len(entry.contents) > 1:
                raise NotImplementedError(
                    'The builder does not currently support this use case.'
                )
            links = self._page_to_links(entry)
            try:
                children = sum([e.members for e in entry.contents], [])
            except AttributeError:
                children = []
            if children:
                links = sum(
                    [
                        #self._sidebar_entry([child])
                        self._page_to_links(child)
                        for child in children
                    ],
                    links,
                )
                contents.extend([{
                    'section': entry.contents[0].name,
                    'contents': links,
                }])
            else:
                contents.extend(links)

        subcontents = [
            self._sidebar_entry([subsection])
            for subsection in subsections
        ]
        return {
            'section': section.title,
            'contents': contents + subcontents,
        }


    def _generate_sidebar(self, blueprint: quartodoc.layout.Layout):
        contents = [f"{self.dir}/index{self.out_page_suffix}"]
        sections = []
        cur_section = None
        for section in blueprint.sections:
            if section.title:
                if cur_section:
                    sections.append(cur_section)
                cur_section = [section]
            elif section.subtitle:
                cur_section.append(section)
        if cur_section:
            sections.append(cur_section)
        for section in sections:
            contents.append(self._sidebar_entry(section))

        entries = [{"id": self.dir, "contents": contents}, {"id": "dummy-sidebar"}]
        return {"website": {"sidebar": entries}}


class HRenderer(quartodoc.MdRenderer):
    @dispatch
    def summarize(self, obj: Object | Alias) -> str:
        # Shamelessly ripped from
        # https://github.com/posit-dev/py-shiny/blob/main/docs/_renderer.py
        # because we have the same problem and want the same solution
        doc = obj.docstring
        if doc is None:
            docstring_parts = []
        else:
            docstring_parts = doc.parsed
        if not len(docstring_parts) or not isinstance(
            docstring_parts[0], DocstringSectionText
        ):
            return ''

        description = docstring_parts[0].value
        short_parts: list[str] = []
        parts = description.split('\n')
        for part in parts:
            if not part.strip():
                break
            short_parts.append(part)
        short = " ".join(short_parts)
        short = quartodoc.renderers.base.convert_rst_link_to_md(short)
        if short[-1] == r'\\':
            short = short[:-1]
        return short

    @staticmethod
    def _fmt_src_link(src_link: str) -> str:
        return f'\n<div style="text-align: right; font-family: monospace;">[[source]]({src_link})</div>'

    def _src_link(self, el: quartodoc.layout.Doc) -> str:
        try:
            start, end = (el.obj.lineno, el.obj.endlineno)
            parts = el.anchor.split('.')
            root = Path(importlib.import_module(parts[0]).__file__).parent
            mod = importlib.import_module('.'.join(parts[:-1]))
            try: # Try to get the source file of the object
                srcfile = inspect.getsourcefile(getattr(mod, parts[-1]))
                srcfile = Path(srcfile).relative_to(root)
            except (ValueError, TypeError): # Fall back to the module file
                srcfile = mod.__file__
                srcfile = Path(srcfile).relative_to(root)
            code_url = SRCREPO.format(lib=parts[0])
            src_link = self._fmt_src_link(
                f'{code_url}/{srcfile}#L{start}-L{end}'
            )
        except ModuleNotFoundError:
            src_link = ''
        return src_link

    @dispatch
    def render(
        self,
        el: quartodoc.layout.DocClass | quartodoc.layout.DocModule,
    ) -> str:
        # This one is copied pretty much verbatim from the base class
        title = self.render_header(el)

        attr_docs = []
        meth_docs = []
        class_docs = []

        if el.members:
            sub_header = "#" * (self.crnt_header_level + 1)
            raw_attrs = [x for x in el.members if x.obj.is_attribute]
            raw_meths = [x for x in el.members if x.obj.is_function]
            raw_classes = [x for x in el.members if x.obj.is_class]

            header = "| Name | Description |\n| --- | --- |"

            # attribute summary table ----
            # docstrings can define an attributes section. If that exists on
            # then we skip summarizing each attribute into a table.
            # TODO: for now, we skip making an attribute table on classes, unless
            # they contain an attributes section in the docstring
            if (
                raw_attrs
                and not _has_attr_section(el.obj.docstring)
                # TODO: what should backwards compat be?
                # and not isinstance(el, layout.DocClass)
            ):

                _attrs_table = "\n".join(map(self.summarize, raw_attrs))
                attrs = f"{sub_header} Attributes\n\n{header}\n{_attrs_table}"
                attr_docs.append(attrs)

            # classes summary table ----
            if raw_classes:
                _summary_table = "\n".join(map(self.summarize, raw_classes))
                section_name = "Classes"
                objs = f"{sub_header} {section_name}\n\n{header}\n{_summary_table}"
                class_docs.append(objs)

                n_incr = 1 if el.flat else 2
                with self._increment_header(n_incr):
                    class_docs.extend(
                        [
                            self.render(x)
                            for x in raw_classes
                            if isinstance(x, quartodoc.layout.Doc)
                        ]
                    )

            # method summary table ----
            if raw_meths:
                _summary_table = "\n".join(map(self.summarize, raw_meths))
                _summary_table = _summary_table + '\n: {tbl-colwidths="[1,2]"}'
                section_name = (
                    "Methods"
                    if isinstance(el, quartodoc.layout.DocClass)
                    else "Functions"
                )
                objs = f"{sub_header} {section_name}\n\n{header}\n{_summary_table}"
                meth_docs.append(objs)

                # TODO use context manager, or context variable?
                n_incr = 1 if el.flat else 2
                with self._increment_header(n_incr):
                    meth_docs.extend(
                        [
                            self.render(x)
                            for x in raw_meths
                            if isinstance(x, quartodoc.layout.Doc)
                        ]
                    )

        str_sig = self.signature(el)
        sig_part = [str_sig] if self.show_signature else []

        with self._increment_header():
            body = self.render(el.obj)

        # This conditional is the only change from the base class
        if isinstance(el.obj, Class):
            src_link = self._src_link(el)
            title = title + src_link

        return "\n\n".join(
            [title, *sig_part, body, *attr_docs, *class_docs, *meth_docs]
        )

    @dispatch
    def render(
        self,
        el: quartodoc.layout.DocFunction | quartodoc.layout.DocAttribute,
    ) -> str:
        title = self.render_header(el)

        str_sig = self.signature(el)
        sig_part = [str_sig] if self.show_signature else []

        if isinstance(el.obj, (Function, Alias)):
            src_link = self._src_link(el)
            title = title + src_link

        with self._increment_header():
            body = self.render(el.obj)

        return "\n\n".join([title, *sig_part, body])

    @staticmethod
    def bind_annot_default(
        name: str,
        annot: str | None,
        default: str | None,
    ) -> str:
        if annot is None:
            if default is None:
                return name
            return f'{name} = {default}'
        if default is None:
            return f'{name}: {annot}'
        return f'{name}: {annot} = {default}'

    @staticmethod
    def param_annot(queries: List[str], params: Parameters):
        for query in queries:
            default = None
            if '=' in query:
                query, default = query.split('=')
            if query not in params:
                yield HRenderer.bind_annot_default(query, None, default)
                continue
            param = params[query]
            annot = str(param.annotation)
            yield HRenderer.bind_annot_default(query, annot, default)

    @dispatch
    def render(self, el: Parameters) -> "list":
        pars = super().render(el)
        return list(HRenderer.param_annot(pars, el))

    @dispatch
    def render(self, el: DocstringSectionSeeAlso):
        entities = []
        cur_entity = []
        for e in el.value.split('\n'):
            if not e:
                continue
            elif e[:4] != '    ':  # New entity
                if cur_entity:
                    entities.append(cur_entity)
                cur_entity = [e]
            else:
                cur_entity.append(e)
        if cur_entity:
            entities.append(cur_entity)
        contents = (
            '\n'.join(
                [
                    ' | '.join(
                        ('', e[0], ' '.join(f.strip() for f in e[1:]), '')
                    )
                    for e in entities
                ]
            )
        )
        table = (
            f'|     | Description |\n|-----|-------------|\n{contents}\n'
        ) + ': {.striped .hover}'
        return quartodoc.renderers.base.convert_rst_link_to_md(table)


def main():
    # Path to the root of the documentation
    root = Path(__file__).parent.parent
    # Clean rendered files
    rmtree(root / '_build', ignore_errors=True)
    # Read the file containing the library names
    with open(root / '_scripts' / '_pkgidx.yml') as f:
        libraries = yaml.safe_load(f)
    # Read the YAML config template
    with open(root / '_source' / '_quartodoc.yml') as f:
        template = f.read()
    # Iterate over each library
    for library, libmeta in libraries.items():
        rmtree(root / '_source' / library / '.quarto', ignore_errors=True)
        # Create a YAML config for the library
        with open(root / '_source' / library / '_quartodoc.yml') as f:
            libspec = f.read()
        libspec = libspec.format(
            __PKGNAME__=library,
            __PKGDESC__=libmeta['description'],
        )
        libspec = template.format(
            __PKGNAME__=library,
            __PKGDESC__=libmeta['description'],
            __PKGSPEC__=libspec,
        )
        _libspec = StringIO()
        _libspec.write(libspec)
        _libspec.seek(0)
        libspec = yaml.safe_load(_libspec)
        if libspec.get('static-resources'):
            static_src = root / '_static'
            static_dst = root / '_source' / library / '_static'
            Path(static_dst).mkdir(exist_ok=True)
            for src in libspec['static-resources']:
                (static_dst / src).parent.mkdir(exist_ok=True, parents=True)
                dst = static_dst / src
                src = static_src / src
                copy(src, dst)
            del libspec['static-resources']
        if not (
            root / '_source' / library / libspec['website']['navbar']['logo']
        ).exists():
            libspec['website']['navbar']['title'] = library
            del libspec['website']['navbar']['logo']
        rmtree(
            root / '_source' / library / libspec['quartodoc']['dir'],
            ignore_errors=True,
        )
        # Build the documentation
        cfgyml = root / '_source' / library / '_quarto.yml'
        with open(cfgyml, 'w+') as f:
            yaml.dump(libspec, f, sort_keys=False)
        sys.path.append(str(cfgyml.parent.absolute()))
        builder = quartodoc.Builder.from_quarto_config(str(cfgyml))
        # I can't figure out how to override the renderer, so I'm doing this
        # hacky thing instead
        renderer = HRenderer()
        renderer.__dict__.update(builder.renderer.__dict__)
        builder.renderer = renderer
        with chdir(cfgyml.parent):
            # blueprint = quartodoc.blueprint(
            #     builder.layout,
            #     dynamic=builder.dynamic,
            #     parser=builder.parser,
            # )
            # pages, builder.items = quartodoc.collect(
            #     blueprint,
            #     base_dir=builder.dir,
            # )
            try:
                builder.build()
            except quartodoc.builder.utils.WorkaroundKeyError as e:
                warnings.warn(
                    f'Failed to build {library}, potentially because '
                    'the library on PyPI is stale. Skipping.\n'
                    f'Original error: {e}'
                )
        # run(['quartodoc', 'build'], cwd=root / '_source' / library)
        run(
            ['quarto', 'render', '--cache-refresh', '--no-clean'],
            cwd=root / '_source' / library,
        )
        (root / '_source' / library / '_quarto.yml').unlink()
    run(
        ['quarto', 'render', '--cache-refresh', '--no-clean'],
        cwd=root / '_source',
    )


if __name__ == "__main__":
    main()
