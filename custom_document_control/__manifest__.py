{
    'name': 'Control de Documentos y Multimedia (ISO/Calidad)',
    'version': '19.0.1.0.0',
    'summary': 'Gestión de ciclo de vida de documentos y multimedia, con y sin flujo ISO',
    'author': 'Nicolas Villalba',
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
	'wizard/document_reject_wizard_views.xml',
	'views/report_certificate.xml',
        'views/document_control_views.xml',
        'views/menu_views.xml',
    ],
    'installable': True,
    'application': True,
}
