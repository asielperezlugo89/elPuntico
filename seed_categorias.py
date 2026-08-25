"""
Seed de categorías y subcategorías para El Puntico.
Ejecutar: python seed_categorias.py
"""
import sqlite3
import os

CATEGORIAS = {
    "Alimentos y Bebidas": [
        "Carnes y Aves",
        "Frutas y Verduras",
        "Lácteos y Huevos",
        "Panadería y Repostería",
        "Bebidas Alcohólicas",
        "Bebidas No Alcohólicas",
        "Snacks y Dulces",
        "Enlatados y Conservas",
        "Cereales y Granos",
        "Condimentos y Especias",
        "Congelados",
        "Productos Orgánicos",
        "Aceites y Vinagres",
        "Pastas y Arroces",
        "Café y Té",
    ],
    "Electrónica": [
        "Celulares y Accesorios",
        "Computadoras y Laptops",
        "Tablets",
        "Audio y Video",
        "Cámaras y Fotografía",
        "Videojuegos",
        "Smart Home",
        "Wearables y Relojes Inteligentes",
        "Cargadores y Baterías",
        "Cables y Adaptadores",
        "Almacenamiento (USB, Discos, SD)",
        "Monitores y Pantallas",
        "Impresoras",
        "Redes y Wi-Fi",
        "Componentes de PC",
    ],
    "Hogar y Decoración": [
        "Muebles",
        "Decoración de Interiores",
        "Iluminación",
        "Cocina",
        "Baño",
        "Jardín y Exteriores",
        "Herramientas",
        "Organización y Almacenamiento",
        "Ropa de Cama",
        "Cortinas y Toldos",
        "Cuadros y Arte Decorativo",
        "Relojes de Pared",
        "Velas y Aromatizadores",
        "Alfombras",
        "Cojines y Mantas",
    ],
    "Moda y Accesorios": [
        "Ropa de Hombre",
        "Ropa de Mujer",
        "Ropa de Niños",
        "Ropa Deportiva",
        "Calzado",
        "Bolsos y Maletas",
        "Joyería y Relojes",
        "Gafas de Sol",
        "Cinturones y Accesorios",
        "Sombreros y Gorras",
        "Bufandas y Guantes",
        "Trajes y Vestidos Formales",
        "Ropa Interior",
        "Pijamas y Ropa de Dormir",
        "Accesorios para el Cabello",
    ],
    "Belleza y Cuidado Personal": [
        "Cuidado de la Piel",
        "Cuidado del Cabello",
        "Maquillaje",
        "Perfumes y Fragancias",
        "Higiene Personal",
        "Afeitado y Cuidado Facial",
        "Uñas y Manicure",
        "Cuidado Dental",
        "Desodorantes y Antitranspirantes",
        "Protector Solar",
        "Maletines de Belleza",
        "Cuidado de Pies",
        "Aceites Esenciales",
    ],
    "Deportes y Recreación": [
        "Fitness y Gimnasio",
        "Fútbol",
        "Béisbol",
        "Baloncesto",
        "Ciclismo",
        "Camping y Senderismo",
        "Pesca",
        "Natación",
        "Yoga y Pilates",
        "Correr y Atletismo",
        "Artes Marciales",
        "Tenis",
        "Voleibol",
        "Skateboarding",
        "Ajedrez y Juegos de Mesa Deportivos",
    ],
    "Salud y Bienestar": [
        "Vitaminas y Suplementos",
        "Equipos Médicos",
        "Primeros Auxilios",
        "Cuidado del Bebé",
        "Medicinas Naturales",
        "Termómetros y Monitores",
        "Mascarillas y Protección",
        "Sillas de Ruedas y Movilidad",
        "Terapia Física",
        "Rehabilitación",
        "Salud Sexual",
        "Cuidado de la Salud Mental",
    ],
    "Mascotas": [
        "Alimento para Perros",
        "Alimento para Gatos",
        "Alimento para Aves",
        "Alimento para Peces",
        "Juguetes para Mascotas",
        "Camas y Transportadoras",
        "Correas y Collares",
        "Higiene y Aseo de Mascotas",
        "Salud Veterinaria",
        "Accesorios para Perros",
        "Accesorios para Gatos",
        "Acuarios y Terrarios",
    ],
    "Bebés y Niños": [
        "Ropa de Bebé",
        "Juguetes Educativos",
        "Juguetes Infantiles",
        "Alimentación del Bebé",
        "Pañales y Toallitas",
        "Cuna y Cama",
        "Sillas de Bebé",
        "Carriolas y cochecitos",
        "Seguridad del Hogar para Bebés",
        "Chupetes y Mordedores",
        "Baño del Bebé",
        "Artículos de Maternidad",
    ],
    "Libros y Papelería": [
        "Libros de Ficción",
        "Libros de No Ficción",
        "Libros Infantiles",
        "Libros Académicos",
        "Libros de Autoayuda",
        "Cuadernos y Libretas",
        "Útiles Escolares",
        "Arte y Manualidades",
        "Material de Oficina",
        "Impresión y Papel",
        "Organización de Escritorio",
        "Cómics y Novelas Gráficas",
    ],
    "Vehículos": [
        "Repuestos y Autopartes",
        "Accesorios de Auto",
        "Llantas y Rines",
        "Aceites y Lubricantes",
        "Herramientas Automotrices",
        "Audio y Multimedia para Auto",
        "Limpieza de Auto",
        "Seguridad Vial",
        "Bicicletas y Accesorios",
        "Motos y Accesorios",
        "Carros Eléctricos Infantiles",
        "GPS y Navegadores",
    ],
    "Electrodomésticos": [
        "Cocina (Licuadoras, Ollas, Sartenes)",
        "Refrigeración",
        "Lavado (Lavadoras, Secadoras)",
        "Climatización (Aires, Ventiladores)",
        "Limpieza (Aspiradoras, Planchas)",
        "Pequeños Electrodomésticos",
        "Hornos y Microondas",
        "Purificadores de Agua",
        "Purificadores de Aire",
        "Ventilación",
    ],
    "Ferretería y Construcción": [
        "Pinturas y Acabados",
        "Herramientas Manuales",
        "Herramientas Eléctricas",
        "Tornillería y Fijaciones",
        "Plomería",
        "Electricidad",
        "Seguridad e Iluminación",
        "Materiales de Construcción",
        "Puertas y Ventanas",
        "Pisos y Revestimientos",
    ],
    "Jardinería": [
        "Plantas y Semillas",
        "Macetas y Jardineras",
        "Herramientas de Jardín",
        "Fertilizantes y Abonos",
        "Sistemas de Riego",
        "Decoración de Jardín",
        "Iluminación Exterior",
        "Cercas y Portones",
        "Compost y Vermicompost",
        "Control de Plagas",
    ],
    "Música": [
        "Instrumentos de Cuerda",
        "Instrumentos de Viento",
        "Instrumentos de Percusión",
        "Teclados y Pianos",
        "Guitarras",
        "Accesorios Musicales",
        "Equipos de Sonido",
        "Micrófonos y Audio",
        "DJ y Producción Musical",
        "Partituras y Libros de Música",
    ],
    "Fotografía y Video": [
        "Cámaras DSLR y Mirrorless",
        "Cámaras de Acción (GoPro)",
        "Lentes y Objetivos",
        "Trípodes y Monopodes",
        "Iluminación de Estudio",
        "Drones",
        "Estabilizadores y Gimbals",
        "Filtros y Accesorios",
        "Tarjetas de Memoria",
        "Bolsos y Fundas para Cámaras",
        "Grabadoras de Audio",
        "Teleprompters",
    ],
    "Arte y Manualidades": [
        "Pintura (Óleo, Acrílico, Acuarela)",
        "Lienzos y Tableros",
        "Pinceles y Espátulas",
        "Bocetarios y Cuadernos de Dibujo",
        "Lápices y Marcadores",
        "Escultura y Modelado",
        "Bordido y Costura Creativa",
        "Resina y Epoxi",
        "Scrapbooking",
        "Stickers y Cintas Decorativas",
        "Hilo, Tela y Macramé",
    ],
    "Abarrotes y Limpieza": [
        "Productos de Limpieza del Hogar",
        "Detergentes y Suavizantes",
        "Papel Higiénico y Servilletas",
        "Bolsas de Basura y Residuos",
        "Utensilios de Cocina",
        "Vajilla y Cristalería",
        "Cubertería",
        "Bodega y Alimentos Básicos",
        "Envases y Recipientes",
        "Jabones y Sanitizantes",
    ],
    "Tecnología y Software": [
        "Software de Oficina",
        "Antivirus y Seguridad",
        "Apps y Suscripciones",
        "Cursos Online de Tecnología",
        "Servicios de Hosting",
        "Diseño Gráfico y Multimedia",
        "Programación y Desarrollo",
        "Inteligencia Artificial",
    ],
    "Servicios": [
        "Reparación de Celulares",
        "Reparación de Computadoras",
        "Fumigación y Control de Plagas",
        "Limpieza de Hogar",
        "Plomería y Electricidad",
        "Clases Particulares",
        "Diseño Web y Marketing Digital",
        "Fotografía de Eventos",
        "Transporte y Mudanzas",
        "Catering y Banquetes",
        "Peluquería a Domicilio",
        "Mecánica y Mantenimiento",
    ],
    "Bebidas y Licores": [
        "Ron",
        "Whisky",
        "Vodka",
        "Tequila",
        "Cerveza Artesanal",
        "Vinos",
        "Champagne y Espumantes",
        "Snaps y Licores",
        "Bebidas Energéticas",
        "Agua y Bebidas Naturales",
        "Jugos y Néctares",
        "Refrescos y Gaseosas",
    ],
    "Regalos y Souvenirs": [
        "Regalos para Ella",
        "Regalos para Él",
        "Regalos para Bebés",
        "Regalos para Parejas",
        "Souvenirs Típicos",
        "Cajas de Regalo",
        "Flores y Arreglos Florales",
        "Tarjetas y Globos",
        "Regalos Personalizados",
        "Cupones y Gift Cards",
        "Artículos Religiosos",
        "Veladoras y velas aromáticas",
    ],
    "Seguros y Finanzas": [
        "Seguros de Vida",
        "Seguros de Auto",
        "Seguros de Salud",
        "Préstamos Personales",
        "Inversiones",
        "Asesoría Financiera",
        "Tarjetas de Crédito",
        "Criptomonedas",
    ],
    "Materiales Educativos": [
        "Cursos Presenciales",
        "Cursos Online",
        "Material Didáctico",
        "Juguetes Montessori",
        "Kits Educativos de Ciencia",
        "Idiomas y Certificaciones",
        "Preparación de Exámenes",
        "Material para Profesores",
    ],
    "Agro y Campo": [
        "Semillas Profesionales",
        "Fertilizantes Industriales",
        "Maquinaria Agrícola",
        "Riego Industrial",
        "Ganadería",
        "Avicultura",
        "Apicultura",
        "Invernaderos",
        "Alimentos para Animales de Granja",
        "Herramientas de Campo",
    ],
}

def seed_categorias(db_path):
    """Inserta todas las categorías y subcategorías en la DB."""
    conn = sqlite3.connect(db_path)
    c = conn.cursor()

    # Crear tabla si no existe
    c.execute("""CREATE TABLE IF NOT EXISTS categorias (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nombre TEXT NOT NULL,
        subcategoria TEXT
    )""")

    # Limpiar categorías existentes (opcional, solo si se pide)
    # c.execute("DELETE FROM categorias")

    insertadas = 0
    duplicadas = 0
    for nombre_cat, subcategorias in CATEGORIAS.items():
        for sub in subcategorias:
            try:
                c.execute(
                    "INSERT INTO categorias (nombre, subcategoria) VALUES (?, ?)",
                    (nombre_cat, sub),
                )
                insertadas += 1
            except sqlite3.IntegrityError:
                duplicadas += 1

    conn.commit()
    conn.close()
    print(f"Categorías insertadas: {insertadas}")
    print(f"Duplicadas omitidas: {duplicadas}")
    print(f"Total categorías principales: {len(CATEGORIAS)}")
    total_subs = sum(len(v) for v in CATEGORIAS.values())
    print(f"Total subcategorías: {total_subs}")


if __name__ == "__main__":
    # Buscar la DB en src/ o en la raíz
    base = os.path.dirname(os.path.abspath(__file__))
    db_path = os.path.join(base, "src", "mitienda.db")
    if not os.path.exists(db_path):
        db_path = os.path.join(base, "mitienda.db")
    if not os.path.exists(db_path):
        print("No se encontró mitienda.db, creando en src/")
        db_path = os.path.join(base, "src", "mitienda.db")
        os.makedirs(os.path.dirname(db_path), exist_ok=True)

    print(f"Usando DB: {db_path}")
    seed_categorias(db_path)
