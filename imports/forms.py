import os

from django import forms


MAX_UPLOAD_SIZE = 5 * 1024 * 1024


class TicketCSVUploadForm(forms.Form):
    file = forms.FileField(
        label='Ticket CSV file',
        help_text='CSV only, UTF-8 encoded, maximum 5 MB and 5000 rows.',
    )

    def clean_file(self):
        upload = self.cleaned_data['file']
        filename = upload.name or ''
        extension = os.path.splitext(filename)[1].lower()
        if extension != '.csv':
            raise forms.ValidationError('Only .csv files are accepted.')
        if upload.size > MAX_UPLOAD_SIZE:
            raise forms.ValidationError('The CSV file must not exceed 5 MB.')
        if upload.size == 0:
            raise forms.ValidationError('The uploaded CSV file is empty.')

        content_type = getattr(upload, 'content_type', '').lower()
        if content_type and content_type not in (
            'text/csv',
            'application/csv',
            'application/vnd.ms-excel',
        ):
            raise forms.ValidationError('The uploaded file is not a valid CSV file.')

        upload.seek(0)
        header = upload.read(8192)
        upload.seek(0)
        if b'\x00' in header:
            raise forms.ValidationError('The CSV contains null bytes and was rejected.')
        try:
            header.decode('utf-8-sig')
        except UnicodeDecodeError:
            raise forms.ValidationError('The CSV must be UTF-8 encoded.')
        return upload
