Document files for the upload tests.

guide_pdfa1b.pdf  PDF/A-1b, made with Ghostscript 9.27 from a LaTeX PDF; passes
                  validate 4.2.0's veraPDF check. The only kind of PDF ELSA accepts.
guide_pdfa2b.pdf  PDF/A-2b. A real archival PDF, but PDS and validate only accept
                  PDF/A-1, so upload refuses it.
guide_plain.pdf   An ordinary PDF (pdflatex, three pages). Refused at upload.
guide.txt         Plain 7-bit ASCII text. Accepted as "7-Bit ASCII Text".
