{
    'name': 'Control de Documentos (ISO/Calidad)',
    'version': '19.0.1.0.0',
    'summary': 'Gestión de ciclo de vida de documentos con integración DMS',
    'author': 'Tu Empresa',
    'category': 'Productivity/Documents',
    'license': 'AGPL-3',
    'depends': [
        'base',
        'mail'
    ],
    'data': [
        'security/security_groups.xml',
        'security/ir.model.access.csv',
        'data/ir_sequence_data.xml',
        'views/document_control_views.xml',
        'views/menu_views.xml',
	'views/report_certificate.xml',
    ],
    'installable': True,
    'application': True,
}
